"""Service layer for the background initial mass backfill.

Two responsibilities:
- ``get_backfill_status`` — the mailbox-scoped read backing the live
  "loading…" counter (``GET /mailboxes/{id}/backfill-status``). Local-only.
- ``enqueue_backfill_on_connect`` — enqueues a first-connection backfill from
  the OAuth callback (``complete_account_connect``), gated by the worker flag.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import BackfillJobError, BackfillStatusError
from api.schemas.backfill import BackfillAccountStatus, BackfillStatusListOut
from api.services.backfill_config import (
    backfill_max_emails_per_account,
    is_backfill_worker_enabled,
)
from api.services.services_helpers import (
    ensure_mailbox_access,
    translate_database_error,
)
from database import (
    DatabaseError,
    account_backfill_store,
    account_store,
)


def get_backfill_status(mailbox_id: str, user_id: str) -> BackfillStatusListOut:
    """Return the backfill progress of every account of the mailbox.

    Local-only (one indexed SELECT, no provider call) — intended for frequent
    polling. Accounts without a backfill job are omitted from the list.
    """
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        rows = account_backfill_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected backfill status error (%s): %s", type(exc).__name__, exc)
        raise BackfillStatusError("Failed to load backfill status for the mailbox.") from exc

    accounts: list[BackfillAccountStatus] = []
    active = False
    for row in rows:
        status = row.get("status")
        if status in ("pending", "running"):
            active = True
        accounts.append(BackfillAccountStatus(
            account_id=str(row.get("account_id")),
            status=status,
            fetched_count=int(row.get("fetched_count") or 0),
            target_total=int(row.get("target_total") or 0),
            done=status in ("completed", "failed"),
        ))
    return BackfillStatusListOut(accounts=accounts, active=active)


def enqueue_backfill_on_connect(mailbox_id: str, account_id: str, provider: str) -> None:
    """Enqueue a first-connection backfill for a freshly connected account.

    Gated by ``BACKFILL_WORKER_ENABLED`` in lockstep with the worker: when the
    worker is off nothing is enqueued, so the account falls through to the
    classic synchronous 500-message bootstrap on its first ``sync-metadata``
    instead of being stranded with a job no worker will process (§4.10).

    Only the FIRST connection enqueues: a reconnection (``sync_cursor`` already
    set) does NOT re-download 100k. ``sync_cursor`` is filled only when the
    backfill completes, so a reconnect mid-backfill still sees NULL and calls
    ``enqueue`` again — but the store's ``ON CONFLICT`` no-ops on a
    pending/running/completed job, so it is safe.

    Raises on failure — the caller (``complete_account_connect``) wraps this in
    a try/except that swallows (best-effort, must not fail the OAuth callback).
    """
    if not is_backfill_worker_enabled():
        return
    try:
        sync_cursor = account_store.get_sync_cursor(mailbox_id, account_id)
        if sync_cursor is not None:
            return  # Already synced at least once (reconnection) — do not backfill.
        account_backfill_store.enqueue(
            account_id, mailbox_id, provider, backfill_max_emails_per_account(),
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected backfill enqueue error (%s): %s", type(exc).__name__, exc)
        raise BackfillJobError("Failed to enqueue the first-connection backfill job.") from exc
