from __future__ import annotations

from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from database.queries import account_backfill
from database.repositories import account_backfill_repository as backfill_module
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


_CREATED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fake_job_row(
    account_id: str = "acc1",
    mailbox_id: str = "mb1",
    provider: str = "gmail",
    status: str = "pending",
    target_total: int = 100000,
    fetched_count: int = 0,
    page_cursor: str | None = None,
    initial_sync_cursor: str | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": mailbox_id,
        "provider": provider,
        "status": status,
        "target_total": target_total,
        "fetched_count": fetched_count,
        "page_cursor": page_cursor,
        "initial_sync_cursor": initial_sync_cursor,
        "attempts": 0,
        "last_error": None,
        "created_at": _CREATED,
        "updated_at": _CREATED,
        "completed_at": None,
    }


# ===== enqueue =====


def test_enqueue_executes_insert_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.enqueue("acc1", "mb1", "gmail", 100000)

    sql, params = cursor.executed[0]
    assert sql == account_backfill.ENQUEUE
    assert params == {
        "account_id": "acc1",
        "mailbox_id": "mb1",
        "provider": "gmail",
        "target_total": 100000,
    }


def test_enqueue_noop_zero_rows_is_not_an_error(monkeypatch):
    # A conflicting non-failed job upserts zero rows (the WHERE status='failed'
    # protects it). The store deliberately does NOT check rowcount — that is a
    # valid outcome, not a failure.
    cursor = FakeCursor(rowcounts=[0])
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.enqueue("acc1", "mb1", "gmail", 100000)


def test_enqueue_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, backfill_module, [cursor])

    with pytest.raises(QueryError, match="Failed to enqueue backfill job"):
        backfill_module.account_backfill_store.enqueue("acc1", "mb1", "gmail", 1)


def test_enqueue_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, backfill_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        backfill_module.account_backfill_store.enqueue("acc1", "mb1", "gmail", 1)


# ===== list_by_mailbox =====


def test_list_by_mailbox_happy_path(monkeypatch):
    rows = [_fake_job_row(account_id="acc1"), _fake_job_row(account_id="acc2", status="completed")]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, backfill_module, [cursor])

    result = backfill_module.account_backfill_store.list_by_mailbox("mb1")
    assert [r["account_id"] for r in result] == ["acc1", "acc2"]
    assert result[1]["status"] == "completed"


def test_list_by_mailbox_returns_empty_on_invalid_uuid(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, backfill_module, [cursor])

    assert backfill_module.account_backfill_store.list_by_mailbox("nope") == []


def test_list_by_mailbox_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, backfill_module, [cursor])

    with pytest.raises(QueryError, match="Failed to list backfill jobs by mailbox"):
        backfill_module.account_backfill_store.list_by_mailbox("mb1")


# ===== claim_next_batch =====


def test_claim_next_batch_returns_claimed_rows(monkeypatch):
    rows = [_fake_job_row(account_id="acc1", status="running")]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, backfill_module, [cursor])

    result = backfill_module.account_backfill_store.claim_next_batch(5)
    sql, params = cursor.executed[0]
    assert sql == account_backfill.CLAIM_NEXT_BATCH
    assert params == {"limit": 5}
    assert result[0]["account_id"] == "acc1"
    assert result[0]["status"] == "running"


def test_claim_next_batch_empty_when_no_pending(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, backfill_module, [cursor])

    assert backfill_module.account_backfill_store.claim_next_batch(5) == []


# ===== update_progress =====


def test_update_progress_executes_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.update_progress("acc1", 500, "tok2")
    sql, params = cursor.executed[0]
    assert sql == account_backfill.UPDATE_PROGRESS
    assert params == {"account_id": "acc1", "fetched_count": 500, "page_cursor": "tok2"}


def test_update_progress_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, backfill_module, [cursor])

    with pytest.raises(QueryError, match="Failed to update backfill progress"):
        backfill_module.account_backfill_store.update_progress("acc1", 1, None)


# ===== set_anchor =====


def test_set_anchor_executes_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.set_anchor("acc1", "hist123")
    sql, params = cursor.executed[0]
    assert sql == account_backfill.SET_ANCHOR
    assert params == {"account_id": "acc1", "initial_sync_cursor": "hist123"}


# ===== mark_completed =====


def test_mark_completed_executes_with_params(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.mark_completed("acc1")
    sql, params = cursor.executed[0]
    assert sql == account_backfill.MARK_COMPLETED
    assert params == {"account_id": "acc1"}


# ===== mark_failed =====


def test_mark_failed_stores_error(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.mark_failed("acc1", "wave_fetch_failed")
    sql, params = cursor.executed[0]
    assert sql == account_backfill.MARK_FAILED
    assert params == {"account_id": "acc1", "error": "wave_fetch_failed"}


def test_mark_failed_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, backfill_module, [cursor])

    with pytest.raises(QueryError, match="Failed to mark backfill job failed"):
        backfill_module.account_backfill_store.mark_failed("acc1", "boom")


# ===== reset_running_to_pending =====


def test_reset_running_to_pending_executes(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, backfill_module, [cursor])

    backfill_module.account_backfill_store.reset_running_to_pending()
    sql, _params = cursor.executed[0]
    assert sql == account_backfill.RESET_RUNNING_TO_PENDING


def test_reset_running_to_pending_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, backfill_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        backfill_module.account_backfill_store.reset_running_to_pending()


# ===== reset_retriable_failed_to_pending (auto-recovery reaper) =====


def test_reset_retriable_failed_executes_with_params_and_returns_rowcount(monkeypatch):
    cursor = FakeCursor(rowcounts=[3])
    patch_connection(monkeypatch, backfill_module, [cursor])

    revived = backfill_module.account_backfill_store.reset_retriable_failed_to_pending(5, 60)

    sql, params = cursor.executed[0]
    assert sql == account_backfill.RESET_RETRIABLE_FAILED_TO_PENDING
    assert params == {"max_attempts": 5, "backoff_seconds": 60}
    # The revived-job count is returned so the worker can log it.
    assert revived == 3


def test_reset_retriable_failed_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, backfill_module, [cursor])

    with pytest.raises(QueryError, match="reset retriable failed backfill"):
        backfill_module.account_backfill_store.reset_retriable_failed_to_pending(5, 60)


def test_reset_retriable_failed_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, backfill_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        backfill_module.account_backfill_store.reset_retriable_failed_to_pending(5, 60)
