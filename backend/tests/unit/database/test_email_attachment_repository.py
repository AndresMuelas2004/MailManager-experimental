"""
Unit tests for ``database.repositories.email_attachment_repository``.

The tests use the shared ``FakeCursor`` / ``FakeConnection`` infrastructure
from ``tests/shared/database_fakes.py``. They verify SQL parameters,
result mapping, error wrapping and the ``ConnectionPoolError`` propagation
invariant documented in the unit guide.
"""

from __future__ import annotations

import pytest

import psycopg2
import psycopg2.errors

from database.errors import ConnectionPoolError, QueryError
from database.repositories import email_attachment_repository as repo_module
from database.repositories.email_attachment_repository import PgEmailAttachmentStore
from tests.shared.database_fakes import (
    FakeCursor,
    patch_connection,
    patch_connection_error,
)


_ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
_PROVIDER_MESSAGE_ID = "msg-1"
_ATTACHMENT_ID = "22222222-2222-2222-2222-222222222222"


# ── list_by_message ────────────────────────────────────────────────


class TestListByMessage:

    def test_returns_normalised_rows(self, monkeypatch):
        rows = [
            {
                "attachment_id": _ATTACHMENT_ID,
                "account_id": _ACCOUNT_ID,
                "provider_message_id": _PROVIDER_MESSAGE_ID,
                "filename": "a.pdf",
                "mime_type": "application/pdf",
                "size": 100,
                "position": 0,
                "is_inline": False,
                "is_downloaded": True,
                "unavailable_at": None,
            },
        ]
        cursor = FakeCursor(fetchall_results=[rows])
        patch_connection(monkeypatch, repo_module, [cursor])

        store = PgEmailAttachmentStore()
        result = store.list_by_message(_ACCOUNT_ID, _PROVIDER_MESSAGE_ID)
        assert len(result) == 1
        # UUIDs are coerced to str by _row_to_dict.
        assert isinstance(result[0]["attachment_id"], str)
        assert isinstance(result[0]["account_id"], str)
        assert result[0]["filename"] == "a.pdf"

    def test_invalid_uuid_returns_empty_list(self, monkeypatch):
        # psycopg2 raises InvalidTextRepresentation for malformed UUIDs;
        # the repo soft-falls-back to [] rather than 500'ing the request.
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation(
                "invalid input syntax for type uuid",
            ),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.list_by_message("not-a-uuid", _PROVIDER_MESSAGE_ID) == []

    def test_db_error_wrapped_into_query_error(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.OperationalError("db down"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError):
            store.list_by_message(_ACCOUNT_ID, _PROVIDER_MESSAGE_ID)

    def test_connection_pool_error_propagates_unchanged(self, monkeypatch):
        # Pool-exhaustion errors are signalled as DatabaseError; the repo's
        # `except DatabaseError: raise` guard must let them propagate.
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgEmailAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.list_by_message(_ACCOUNT_ID, _PROVIDER_MESSAGE_ID)

    def test_unexpected_exception_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError):
            store.list_by_message(_ACCOUNT_ID, _PROVIDER_MESSAGE_ID)


# ── get_for_download ───────────────────────────────────────────────


class TestGetForDownload:

    def test_returns_row_when_found(self, monkeypatch):
        row = {
            "attachment_id": _ATTACHMENT_ID,
            "account_id": _ACCOUNT_ID,
            "filename": "a.pdf",
            "size": 1,
        }
        cursor = FakeCursor(fetchone_results=[row])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        result = store.get_for_download(
            user_id="u", mailbox_id="m", account_id=_ACCOUNT_ID, attachment_id=_ATTACHMENT_ID,
        )
        assert result is not None
        assert isinstance(result["attachment_id"], str)

    def test_returns_none_when_no_row(self, monkeypatch):
        cursor = FakeCursor()  # fetchone returns None
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_for_download(
            user_id="u", mailbox_id="m", account_id=_ACCOUNT_ID, attachment_id=_ATTACHMENT_ID,
        ) is None

    def test_invalid_uuid_returns_none(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_for_download(
            user_id="u", mailbox_id="m", account_id=_ACCOUNT_ID, attachment_id="not-a-uuid",
        ) is None

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgEmailAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.get_for_download(
                user_id="u", mailbox_id="m", account_id=_ACCOUNT_ID, attachment_id=_ATTACHMENT_ID,
            )


# ── get_blob ───────────────────────────────────────────────────────


class TestGetBlob:

    def test_returns_bytes(self, monkeypatch):
        cursor = FakeCursor(fetchone_results=[(b"PDF",)])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_blob(_ATTACHMENT_ID) == b"PDF"

    def test_no_row_returns_none(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_blob(_ATTACHMENT_ID) is None

    def test_null_blob_returns_none(self, monkeypatch):
        cursor = FakeCursor(fetchone_results=[(None,)])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_blob(_ATTACHMENT_ID) is None

    def test_invalid_uuid_returns_none(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        assert store.get_blob("not-a-uuid") is None


# ── insert_blob ────────────────────────────────────────────────────


class TestInsertBlob:

    def test_passes_psycopg2_binary(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        store.insert_blob(_ATTACHMENT_ID, b"PDF")
        assert len(cursor.executed) == 1
        sql, params = cursor.executed[0]
        # Sanity: SQL touches the right table; the blob is wrapped as
        # ``psycopg2.Binary`` for parameterised insertion.
        assert "email_attachment_blobs" in sql
        assert params["attachment_id"] == _ATTACHMENT_ID
        assert isinstance(params["blob"], psycopg2.extensions.Binary)

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError):
            store.insert_blob(_ATTACHMENT_ID, b"PDF")

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgEmailAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.insert_blob(_ATTACHMENT_ID, b"PDF")


# ── mark_unavailable / touch_last_accessed ────────────────────────


class TestStateTransitions:

    def test_mark_unavailable_executes_update(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        store.mark_unavailable(_ATTACHMENT_ID)
        assert len(cursor.executed) == 1
        sql, params = cursor.executed[0]
        assert "unavailable_at" in sql
        assert params["attachment_id"] == _ATTACHMENT_ID

    def test_touch_last_accessed_executes_update(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        store.touch_last_accessed(_ATTACHMENT_ID)
        assert len(cursor.executed) == 1
        sql, _params = cursor.executed[0]
        assert "last_accessed_at" in sql

    def test_mark_unavailable_invalid_uuid_silently_returns(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        # No raise — the record may have been deleted concurrently.
        store.mark_unavailable("not-a-uuid")

    def test_touch_last_accessed_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError):
            store.touch_last_accessed(_ATTACHMENT_ID)


# ── purge_expired_blobs ────────────────────────────────────────────


class TestPurgeExpiredBlobs:

    def test_returns_count_and_freed_bytes(self, monkeypatch):
        # Each row: (attachment_id, bytes)
        rows = [("a1", 100), ("a2", 200), ("a3", 50)]
        cursor = FakeCursor(fetchall_results=[rows])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        count, freed = store.purge_expired_blobs()
        assert count == 3
        assert freed == 350

    def test_zero_purge_returns_zero(self, monkeypatch):
        cursor = FakeCursor(fetchall_results=[[]])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        count, freed = store.purge_expired_blobs()
        assert count == 0
        assert freed == 0

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError):
            store.purge_expired_blobs()

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgEmailAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.purge_expired_blobs()


# ── upsert_batch ───────────────────────────────────────────────────


def _gmail_row(attachment_id: str = _ATTACHMENT_ID, position: int = 0) -> dict:
    return {
        "attachment_id": attachment_id,
        "account_id": _ACCOUNT_ID,
        "provider_message_id": _PROVIDER_MESSAGE_ID,
        "part_id": "1",
        "provider_attachment_id": None,
        "filename": "g.pdf",
        "mime_type": "application/pdf",
        "size": 100,
        "content_id": None,
        "is_inline": False,
        "position": position,
    }


def _outlook_row(attachment_id: str = _ATTACHMENT_ID, position: int = 0) -> dict:
    return {
        "attachment_id": attachment_id,
        "account_id": _ACCOUNT_ID,
        "provider_message_id": _PROVIDER_MESSAGE_ID,
        "part_id": None,
        "provider_attachment_id": "graph-att-xyz",
        "filename": "o.pdf",
        "mime_type": "application/pdf",
        "size": 200,
        "content_id": None,
        "is_inline": False,
        "position": position,
    }


def _stub_execute_values_returning(returns: list[list[tuple]]):
    """Return a stub that records calls and yields ``returns`` entries in order."""
    captured: list[tuple] = []
    iterator = iter(returns)

    def stub(cur, sql, rows, **kwargs):
        captured.append((sql, list(rows)))
        cur.executed.append((sql, rows))
        return next(iterator, [])

    return stub, captured


class TestUpsertBatch:

    def test_empty_input_returns_empty_without_touching_connection(self, monkeypatch):
        # No FakeCursor patched — any DB access would raise.
        store = PgEmailAttachmentStore()
        assert store.upsert_batch([]) == []

    def test_gmail_only_batch_hits_gmail_query(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        stub, captured = _stub_execute_values_returning([[(_ATTACHMENT_ID,)]])
        monkeypatch.setattr(repo_module.psycopg2.extras, "execute_values", stub)

        store = PgEmailAttachmentStore()
        persisted = store.upsert_batch([_gmail_row()])

        assert persisted == [_ATTACHMENT_ID]
        assert len(captured) == 1
        # Identify the Gmail variant by its conflict key, which embeds part_id.
        from database.queries import email_attachments as att_queries
        assert captured[0][0] == att_queries.UPSERT_EMAIL_ATTACHMENTS_GMAIL

    def test_outlook_only_batch_hits_outlook_query(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        stub, captured = _stub_execute_values_returning([[(_ATTACHMENT_ID,)]])
        monkeypatch.setattr(repo_module.psycopg2.extras, "execute_values", stub)

        store = PgEmailAttachmentStore()
        persisted = store.upsert_batch([_outlook_row()])

        assert persisted == [_ATTACHMENT_ID]
        assert len(captured) == 1
        from database.queries import email_attachments as att_queries
        assert captured[0][0] == att_queries.UPSERT_EMAIL_ATTACHMENTS_OUTLOOK

    def test_mixed_batch_runs_both_partitions_in_order(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        gmail_id = "33333333-3333-3333-3333-333333333333"
        outlook_id = "44444444-4444-4444-4444-444444444444"
        stub, captured = _stub_execute_values_returning(
            [[(gmail_id,)], [(outlook_id,)]],
        )
        monkeypatch.setattr(repo_module.psycopg2.extras, "execute_values", stub)

        store = PgEmailAttachmentStore()
        persisted = store.upsert_batch([
            _gmail_row(attachment_id=gmail_id),
            _outlook_row(attachment_id=outlook_id),
        ])

        # Gmail partition first, then Outlook — order matters for assertion.
        assert persisted == [gmail_id, outlook_id]
        assert len(captured) == 2

    def test_psycopg2_error_wraps_to_query_error(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])

        def _raise(cur, sql, rows, **kwargs):
            raise psycopg2.OperationalError("connection lost")

        monkeypatch.setattr(repo_module.psycopg2.extras, "execute_values", _raise)
        store = PgEmailAttachmentStore()
        with pytest.raises(QueryError, match="Failed to upsert email attachments"):
            store.upsert_batch([_gmail_row()])

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgEmailAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.upsert_batch([_gmail_row()])
