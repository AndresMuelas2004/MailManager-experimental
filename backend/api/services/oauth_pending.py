"""
In-memory registry of pending interactive OAuth connect flows.

The connect flow is split in two HTTP steps (start → provider redirect →
callback). Between the two, the provider-specific ``flow_state`` (which may
hold live, non-serializable objects such as a google-auth Flow carrying the
PKCE verifier) must survive in the API process. This registry keeps it keyed
by the OAuth ``state`` token.

Process-local by design: it assumes a single uvicorn worker (the project's
runtime). A multi-worker deployment would need a shared store and a
serializable ``flow_state`` instead.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Generous upper bound for the user to finish the provider consent screens.
_TTL_SECONDS = 600.0

_lock = threading.Lock()
_pending: dict[str, "PendingConnect"] = {}


@dataclass
class PendingConnect:
    """One in-flight connect flow, registered at start, popped at callback."""

    state: str
    mailbox_id: str
    account_id: str
    user_id: str
    provider: str
    account_label: str
    flow_state: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)


def register(entry: PendingConnect) -> None:
    """
    Register a pending flow, evicting any previous flow for the same account.

    Only the most recent authorization URL can complete: a user who clicks
    "add" twice must not be able to finish the stale first popup against the
    second registration.
    """
    with _lock:
        _purge_expired_locked()
        stale_states = [
            state for state, pending in _pending.items()
            if pending.account_id == entry.account_id
        ]
        for state in stale_states:
            _pending.pop(state, None)
        _pending[entry.state] = entry


def pop(state: str) -> PendingConnect | None:
    """
    Remove and return the pending flow for *state* (single-use), or None
    when the state is unknown or expired.
    """
    with _lock:
        _purge_expired_locked()
        return _pending.pop(state, None)


def _purge_expired_locked() -> None:
    now = time.monotonic()
    expired = [
        state for state, pending in _pending.items()
        if now - pending.created_at > _TTL_SECONDS
    ]
    for state in expired:
        _pending.pop(state, None)
