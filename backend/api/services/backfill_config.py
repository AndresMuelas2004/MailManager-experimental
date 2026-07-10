"""Environment configuration for the background initial mass backfill.

Leaf module shared by the worker (``backfill_worker``), the enqueue/status
service (``backfill_service``) and the app lifespan. Env vars are read at the
point of use (same pattern as ``GMAIL_BATCH_MAX_WORKERS`` in core and
``RATE_LIMIT_ENABLED`` in ``api.rate_limit``) so tests can toggle them with
``monkeypatch.setenv`` without rebuilding anything.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, defaulting to %d", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, defaulting to %s", name, raw, default)
        return default


def is_backfill_worker_enabled() -> bool:
    """Kill-switch, default ON. Gates BOTH the dispatcher thread startup AND
    the enqueue on connect (in lockstep) — the sync guard is deliberately
    independent of this flag."""
    return os.environ.get("BACKFILL_WORKER_ENABLED", "true").strip().lower() in _TRUE_VALUES


def backfill_max_emails_per_account() -> int:
    """Per-account target size of the backfill (the ``target_total``)."""
    return _int_env("BACKFILL_MAX_EMAILS_PER_ACCOUNT", 100000)


def backfill_max_concurrent() -> int:
    """Number of accounts backfilled in parallel by the worker pool."""
    return _int_env("BACKFILL_MAX_CONCURRENT", 2)


def backfill_gmail_gets_per_minute() -> int:
    """Target Gmail ``messages.get`` rate (conservative default ~= 6000
    quota units/min / 20 units per get). Tuned empirically in validation."""
    return _int_env("BACKFILL_GMAIL_GETS_PER_MINUTE", 300)


def backfill_outlook_page_delay_ms() -> int:
    """Delay between Outlook backfill pages (metadata comes inline, so pages
    are cheap; a small delay keeps well under the per-mailbox throttle)."""
    return _int_env("BACKFILL_OUTLOOK_PAGE_DELAY_MS", 300)


def backfill_poll_interval_s() -> float:
    """Dispatcher poll interval for claiming pending jobs."""
    return _float_env("BACKFILL_POLL_INTERVAL_S", 5.0)
