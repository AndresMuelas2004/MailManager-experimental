"""
Unit tests for the in-memory pending OAuth-connect registry.
"""

from __future__ import annotations

import pytest

from api.services import oauth_pending


@pytest.fixture(autouse=True)
def _clean_registry():
    oauth_pending._pending.clear()
    yield
    oauth_pending._pending.clear()


def _entry(state: str, account_id: str = "acc-1", created_at: float | None = None) -> oauth_pending.PendingConnect:
    kwargs = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
    return oauth_pending.PendingConnect(
        state=state,
        mailbox_id="mb-1",
        account_id=account_id,
        user_id="user-1",
        provider="gmail",
        account_label=f"mb-1__{account_id}",
        flow_state={"fake": True},
        **kwargs,
    )


def test_pop_returns_entry_once(monkeypatch):
    oauth_pending.register(_entry("state-a"))
    first = oauth_pending.pop("state-a")
    assert first is not None
    assert first.account_id == "acc-1"
    assert oauth_pending.pop("state-a") is None


def test_pop_unknown_state_returns_none():
    assert oauth_pending.pop("missing") is None


def test_register_evicts_previous_flow_for_same_account():
    oauth_pending.register(_entry("state-old", account_id="acc-1"))
    oauth_pending.register(_entry("state-new", account_id="acc-1"))
    assert oauth_pending.pop("state-old") is None
    assert oauth_pending.pop("state-new") is not None


def test_register_keeps_flows_of_other_accounts():
    oauth_pending.register(_entry("state-a", account_id="acc-1"))
    oauth_pending.register(_entry("state-b", account_id="acc-2"))
    assert oauth_pending.pop("state-a") is not None
    assert oauth_pending.pop("state-b") is not None


def test_expired_entry_is_purged(monkeypatch):
    oauth_pending.register(_entry("state-a"))
    entry = oauth_pending._pending["state-a"]
    entry.created_at -= oauth_pending._TTL_SECONDS + 1
    assert oauth_pending.pop("state-a") is None
