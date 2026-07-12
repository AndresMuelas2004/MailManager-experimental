"""Unit tests for ``PgDraftSyncStore`` — the server-side draft-sync job queue.

A simplified clone of ``account_backfill_repository`` WITHOUT the pagination
checkpoint. All DB access is faked at the connection boundary (no real DB). The
one behaviour that distinguishes ENQUEUE from the backfill's is that it resets
UNCONDITIONALLY to ``pending`` (no ``WHERE status='failed'`` guard), because a
reconnection must always refresh the drafts.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from database.queries import draft_sync
from database.repositories import draft_sync_repository as draft_module
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


_CREATED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fake_job_row(
    account_id: str = "acc1",
    mailbox_id: str = "mb1",
    provider: str = "gmail",
    status: str = "pending",
    attempts: int = 0,
) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": mailbox_id,
        "provider": provider,
        "status": status,
        "attempts": attempts,
        "last_error": None,
        "created_at": _CREATED,
        "updated_at": _CREATED,
        "completed_at": None,
    }


# ===== enqueue (unconditional reset to pending) =====


def test_enqueue_executes_unconditional_insert_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, draft_module, [cursor])

    draft_module.draft_sync_store.enqueue("acc1", "mb1", "gmail")

    sql, params = cursor.executed[0]
    # The SQL is the unconditional ENQUEUE (no WHERE status='failed' guard).
    assert sql == draft_sync.ENQUEUE
    assert params == {"account_id": "acc1", "mailbox_id": "mb1", "provider": "gmail"}


def test_enqueue_zero_rows_is_not_an_error(monkeypatch):
    # The store does not check rowcount — a re-enqueue is a valid outcome.
    cursor = FakeCursor(rowcounts=[0])
    patch_connection(monkeypatch, draft_module, [cursor])

    draft_module.draft_sync_store.enqueue("acc1", "mb1", "gmail")


def test_enqueue_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, draft_module, [cursor])

    with pytest.raises(QueryError, match="Failed to enqueue draft sync job"):
        draft_module.draft_sync_store.enqueue("acc1", "mb1", "gmail")


def test_enqueue_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, draft_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        draft_module.draft_sync_store.enqueue("acc1", "mb1", "gmail")


# ===== claim_next_batch =====


def test_claim_next_batch_returns_claimed_rows(monkeypatch):
    rows = [_fake_job_row(account_id="acc1", status="running")]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, draft_module, [cursor])

    result = draft_module.draft_sync_store.claim_next_batch(5)
    sql, params = cursor.executed[0]
    assert sql == draft_sync.CLAIM_NEXT_BATCH
    assert params == {"limit": 5}
    assert result[0]["account_id"] == "acc1"
    assert result[0]["status"] == "running"
    # _row_to_dict coerces ids to str and timestamps to ISO strings.
    assert isinstance(result[0]["account_id"], str)
    assert result[0]["created_at"] == _CREATED.isoformat()


def test_claim_next_batch_empty_when_no_pending(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, draft_module, [cursor])

    assert draft_module.draft_sync_store.claim_next_batch(5) == []


# ===== mark_completed / mark_failed =====


def test_mark_completed_executes_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, draft_module, [cursor])

    draft_module.draft_sync_store.mark_completed("acc1")
    sql, params = cursor.executed[0]
    assert sql == draft_sync.MARK_COMPLETED
    assert params == {"account_id": "acc1"}


def test_mark_failed_stores_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, draft_module, [cursor])

    draft_module.draft_sync_store.mark_failed("acc1", "fetch")
    sql, params = cursor.executed[0]
    assert sql == draft_sync.MARK_FAILED
    assert params == {"account_id": "acc1", "error": "fetch"}


def test_mark_failed_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, draft_module, [cursor])

    with pytest.raises(QueryError, match="Failed to mark draft sync job failed"):
        draft_module.draft_sync_store.mark_failed("acc1", "boom")


# ===== reset_running_to_pending (startup recovery) =====


def test_reset_running_to_pending_executes(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, draft_module, [cursor])

    draft_module.draft_sync_store.reset_running_to_pending()
    sql, _params = cursor.executed[0]
    assert sql == draft_sync.RESET_RUNNING_TO_PENDING


def test_reset_running_to_pending_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, draft_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        draft_module.draft_sync_store.reset_running_to_pending()


# ===== reset_retriable_failed_to_pending (auto-recovery reaper) =====


def test_reset_retriable_failed_executes_with_params_and_returns_rowcount(monkeypatch):
    cursor = FakeCursor(rowcounts=[2])
    patch_connection(monkeypatch, draft_module, [cursor])

    revived = draft_module.draft_sync_store.reset_retriable_failed_to_pending(5, 60)

    sql, params = cursor.executed[0]
    assert sql == draft_sync.RESET_RETRIABLE_FAILED_TO_PENDING
    assert params == {"max_attempts": 5, "backoff_seconds": 60}
    assert revived == 2


def test_reset_retriable_failed_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, draft_module, [cursor])

    with pytest.raises(QueryError, match="reset retriable failed draft sync"):
        draft_module.draft_sync_store.reset_retriable_failed_to_pending(5, 60)
