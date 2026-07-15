"""Unit tests for ``api.services.backfill_service`` — the status read and the
first-connection enqueue, both faked at the store boundary (no DB)."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    ApiError,
    BackfillJobError,
    BackfillStatusError,
    DraftSyncError,
    Forbidden,
    MailboxNotFound,
)
from api.services import backfill_service
from database import DatabaseError


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"


class _FakeBackfillStore:
    def __init__(self, rows=None, *, list_exc=None, enqueue_exc=None):
        self._rows = rows or []
        self._list_exc = list_exc
        self._enqueue_exc = enqueue_exc
        self.enqueue_calls: list[tuple] = []

    def list_by_mailbox(self, mailbox_id):
        if self._list_exc:
            raise self._list_exc
        return self._rows

    def enqueue(self, account_id, mailbox_id, provider, target_total):
        if self._enqueue_exc:
            raise self._enqueue_exc
        self.enqueue_calls.append((account_id, mailbox_id, provider, target_total))


class _FakeAccountStore:
    def __init__(self, sync_cursor=None):
        self._sync_cursor = sync_cursor
        self.get_sync_cursor_calls: list[tuple] = []

    def get_sync_cursor(self, mailbox_id, account_id):
        self.get_sync_cursor_calls.append((mailbox_id, account_id))
        return self._sync_cursor


def _patch_access(monkeypatch, *, raises=None):
    def _ensure(_mb, _uid):
        if raises is not None:
            raise raises
        return {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID}

    monkeypatch.setattr(backfill_service, "ensure_mailbox_access", _ensure)


# ===== get_backfill_status =====


class TestGetBackfillStatus:

    def test_shape_active_and_done_flags(self, monkeypatch):
        _patch_access(monkeypatch)
        rows = [
            {"account_id": "acc1", "status": "running", "fetched_count": 120, "target_total": 100000},
            {"account_id": "acc2", "status": "completed", "fetched_count": 5, "target_total": 5},
        ]
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore(rows))

        result = backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)

        # active is True because at least one job is pending/running.
        assert result.active is True
        by_id = {a.account_id: a for a in result.accounts}
        assert by_id["acc1"].status == "running"
        assert by_id["acc1"].fetched_count == 120
        assert by_id["acc1"].done is False
        # completed is a terminal state → done True; it does not set active.
        assert by_id["acc2"].done is True

    def test_active_false_when_only_terminal(self, monkeypatch):
        _patch_access(monkeypatch)
        rows = [
            {"account_id": "acc1", "status": "completed", "fetched_count": 5, "target_total": 5},
            {"account_id": "acc2", "status": "failed", "fetched_count": 2, "target_total": 5},
        ]
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore(rows))

        result = backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)
        assert result.active is False
        assert {a.done for a in result.accounts} == {True}

    def test_pending_counts_as_active(self, monkeypatch):
        _patch_access(monkeypatch)
        rows = [{"account_id": "acc1", "status": "pending", "fetched_count": 0, "target_total": 100000}]
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore(rows))

        result = backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)
        assert result.active is True
        assert result.accounts[0].done is False

    def test_no_jobs_returns_empty_inactive(self, monkeypatch):
        _patch_access(monkeypatch)
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore([]))

        result = backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)
        assert result.accounts == []
        assert result.active is False

    def test_foreign_mailbox_forbidden_propagates(self, monkeypatch):
        _patch_access(monkeypatch, raises=Forbidden("no access"))
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore([]))
        with pytest.raises(Forbidden):
            backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)

    def test_missing_mailbox_not_found_propagates(self, monkeypatch):
        _patch_access(monkeypatch, raises=MailboxNotFound("gone"))
        monkeypatch.setattr(backfill_service, "account_backfill_store", _FakeBackfillStore([]))
        with pytest.raises(MailboxNotFound):
            backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)

    def test_database_error_is_translated_not_wrapped_as_status_error(self, monkeypatch):
        # A store DatabaseError must go through translate_database_error (503
        # family), NOT the generic BackfillStatusError (500) branch.
        _patch_access(monkeypatch)
        monkeypatch.setattr(
            backfill_service, "account_backfill_store",
            _FakeBackfillStore(list_exc=DatabaseError("db down")),
        )
        with pytest.raises(ApiError) as exc_info:
            backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)
        assert not isinstance(exc_info.value, BackfillStatusError)

    def test_unexpected_error_becomes_backfill_status_error(self, monkeypatch):
        _patch_access(monkeypatch)
        monkeypatch.setattr(
            backfill_service, "account_backfill_store",
            _FakeBackfillStore(list_exc=RuntimeError("boom")),
        )
        with pytest.raises(BackfillStatusError):
            backfill_service.get_backfill_status(_MAILBOX_ID, _USER_ID)


# ===== enqueue_backfill_on_connect =====


class TestEnqueueBackfillOnConnect:

    def test_first_connection_enqueues(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        monkeypatch.setenv("BACKFILL_MAX_EMAILS_PER_ACCOUNT", "12345")
        store = _FakeBackfillStore()
        monkeypatch.setattr(backfill_service, "account_backfill_store", store)
        monkeypatch.setattr(backfill_service, "account_store", _FakeAccountStore(sync_cursor=None))

        backfill_service.enqueue_backfill_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")

        assert store.enqueue_calls == [(_ACCOUNT_ID, _MAILBOX_ID, "gmail", 12345)]

    def test_reconnection_does_not_enqueue(self, monkeypatch):
        # sync_cursor already set → the account synced at least once → no backfill.
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        store = _FakeBackfillStore()
        monkeypatch.setattr(backfill_service, "account_backfill_store", store)
        monkeypatch.setattr(
            backfill_service, "account_store", _FakeAccountStore(sync_cursor="hist123"),
        )

        backfill_service.enqueue_backfill_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")
        assert store.enqueue_calls == []

    def test_worker_disabled_never_enqueues_and_skips_cursor_lookup(self, monkeypatch):
        # The worker gate short-circuits BEFORE the sync_cursor lookup, so a
        # brand-new account falls through to the classic bootstrap instead of
        # being stranded with a job no worker will process (§4.10).
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "false")
        store = _FakeBackfillStore()
        account_store = _FakeAccountStore(sync_cursor=None)
        monkeypatch.setattr(backfill_service, "account_backfill_store", store)
        monkeypatch.setattr(backfill_service, "account_store", account_store)

        backfill_service.enqueue_backfill_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")

        assert store.enqueue_calls == []
        assert account_store.get_sync_cursor_calls == []

    def test_enqueue_database_error_is_translated_to_api_error(self, monkeypatch):
        # The service still raises (the OAuth callback wraps this in a swallowing
        # try/except, tested at the accounts_service layer), but a store
        # DatabaseError must go through translate_database_error into a typed
        # ApiError (503 family) rather than escaping raw and untyped.
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        monkeypatch.setattr(
            backfill_service, "account_backfill_store",
            _FakeBackfillStore(enqueue_exc=DatabaseError("insert failed")),
        )
        monkeypatch.setattr(backfill_service, "account_store", _FakeAccountStore(sync_cursor=None))

        with pytest.raises(ApiError) as exc_info:
            backfill_service.enqueue_backfill_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")
        assert not isinstance(exc_info.value, DatabaseError)

    def test_enqueue_unexpected_error_becomes_backfill_job_error(self, monkeypatch):
        # A non-DB failure from the store surfaces as the typed BackfillJobError
        # (500) — still raised to the swallowing caller, never escaping untyped.
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        monkeypatch.setattr(
            backfill_service, "account_backfill_store",
            _FakeBackfillStore(enqueue_exc=RuntimeError("boom")),
        )
        monkeypatch.setattr(backfill_service, "account_store", _FakeAccountStore(sync_cursor=None))

        with pytest.raises(BackfillJobError):
            backfill_service.enqueue_backfill_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")


# ===== enqueue_draft_sync_on_connect =====


class _FakeDraftSyncStore:
    def __init__(self, *, enqueue_exc=None):
        self._enqueue_exc = enqueue_exc
        self.enqueue_calls: list[tuple] = []

    def enqueue(self, account_id, mailbox_id, provider):
        if self._enqueue_exc:
            raise self._enqueue_exc
        self.enqueue_calls.append((account_id, mailbox_id, provider))


class TestEnqueueDraftSyncOnConnect:

    def test_enqueues_on_every_connect(self, monkeypatch):
        # Unlike the backfill, the draft sync is enqueued unconditionally — no
        # sync_cursor lookup — so a reconnection also refreshes the drafts.
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        store = _FakeDraftSyncStore()
        monkeypatch.setattr(backfill_service, "draft_sync_store", store)

        backfill_service.enqueue_draft_sync_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")

        assert store.enqueue_calls == [(_ACCOUNT_ID, _MAILBOX_ID, "gmail")]

    def test_worker_disabled_never_enqueues(self, monkeypatch):
        # Gated by the same flag as the worker in lockstep: with the worker off
        # nothing is enqueued and the frontend POST /drafts/sync stays the fallback.
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "false")
        store = _FakeDraftSyncStore()
        monkeypatch.setattr(backfill_service, "draft_sync_store", store)

        backfill_service.enqueue_draft_sync_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")

        assert store.enqueue_calls == []

    def test_database_error_is_translated_to_api_error(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        monkeypatch.setattr(
            backfill_service, "draft_sync_store",
            _FakeDraftSyncStore(enqueue_exc=DatabaseError("insert failed")),
        )
        with pytest.raises(ApiError) as exc_info:
            backfill_service.enqueue_draft_sync_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")
        assert not isinstance(exc_info.value, DatabaseError)

    def test_unexpected_error_becomes_draft_sync_error(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", "true")
        monkeypatch.setattr(
            backfill_service, "draft_sync_store",
            _FakeDraftSyncStore(enqueue_exc=RuntimeError("boom")),
        )
        with pytest.raises(DraftSyncError):
            backfill_service.enqueue_draft_sync_on_connect(_MAILBOX_ID, _ACCOUNT_ID, "gmail")
