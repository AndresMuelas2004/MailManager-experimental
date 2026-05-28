"""
Unit tests for ``database.repositories.draft_attachment_repository``.

The repository pattern matches ``email_attachment_repository``: shared
``FakeCursor`` / ``FakeConnection`` infrastructure, ``ConnectionPoolError``
must propagate unchanged, ``InvalidTextRepresentation`` soft-falls-back
to ``None`` / ``[]`` / ``False`` depending on the operation.
"""

from __future__ import annotations

import pytest

import psycopg2
import psycopg2.errors
import psycopg2.extras

from database.errors import ConnectionPoolError, QueryError
from database.repositories import draft_attachment_repository as repo_module
from database.repositories.draft_attachment_repository import PgDraftAttachmentStore
from tests.shared.database_fakes import (
    FakeCursor,
    patch_connection,
    patch_connection_error,
)


_DRAFT_ATTACHMENT_ID = "11111111-1111-1111-1111-111111111111"
_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
_PROVIDER_DRAFT_ID = "draft-1"


def _row() -> dict:
    return {
        "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
        "account_id": _ACCOUNT_ID,
        "provider_draft_id": _PROVIDER_DRAFT_ID,
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size": 100,
        "content_id": None,
        "is_inline": False,
        "position": 0,
        "provider_attachment_id": None,
        "created_at": None,
    }


# ── insert ────────────────────────────────────────────────────────


class TestInsert:

    def test_returns_normalised_row(self, monkeypatch):
        cursor = FakeCursor(fetchone_results=[_row()])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        result = store.insert({
            "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
            "account_id": _ACCOUNT_ID,
            "provider_draft_id": _PROVIDER_DRAFT_ID,
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "size": 100,
            "blob": b"PDF",
        })
        assert isinstance(result["draft_attachment_id"], str)
        assert isinstance(result["account_id"], str)
        assert result["filename"] == "report.pdf"

    def test_no_returning_row_raises(self, monkeypatch):
        # Defensive guard — INSERT ... RETURNING must yield a row.
        cursor = FakeCursor()  # fetchone returns None
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.insert({
                "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
                "account_id": _ACCOUNT_ID,
                "provider_draft_id": _PROVIDER_DRAFT_ID,
                "filename": "x",
                "mime_type": "application/octet-stream",
                "size": 1,
                "blob": b"x",
            })

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.insert({
                "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
                "account_id": _ACCOUNT_ID,
                "provider_draft_id": _PROVIDER_DRAFT_ID,
                "filename": "x",
                "mime_type": "application/octet-stream",
                "size": 1,
                "blob": b"x",
            })

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgDraftAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.insert({
                "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
                "account_id": _ACCOUNT_ID,
                "provider_draft_id": _PROVIDER_DRAFT_ID,
                "filename": "x",
                "mime_type": "application/octet-stream",
                "size": 1,
                "blob": b"x",
            })

    def test_blob_wrapped_as_psycopg2_binary(self, monkeypatch):
        cursor = FakeCursor(fetchone_results=[_row()])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        store.insert({
            "draft_attachment_id": _DRAFT_ATTACHMENT_ID,
            "account_id": _ACCOUNT_ID,
            "provider_draft_id": _PROVIDER_DRAFT_ID,
            "filename": "x.pdf",
            "mime_type": "application/pdf",
            "size": 3,
            "blob": b"PDF",
        })
        _, params = cursor.executed[0]
        assert isinstance(params["blob"], psycopg2.extensions.Binary)


# ── list_by_draft ──────────────────────────────────────────────────


class TestListByDraft:

    def test_returns_list_of_rows(self, monkeypatch):
        cursor = FakeCursor(fetchall_results=[[_row(), _row()]])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        result = store.list_by_draft(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)
        assert len(result) == 2

    def test_invalid_uuid_returns_empty(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.list_by_draft("not-a-uuid", _PROVIDER_DRAFT_ID) == []

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgDraftAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.list_by_draft(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)


# ── list_by_draft_with_blob ────────────────────────────────────────


class TestListByDraftWithBlob:

    def test_returns_rows_with_blob_as_bytes(self, monkeypatch):
        # The DB returns blobs as memoryview; the repository's _row_to_dict
        # casts them to plain bytes so the service sees a uniform shape.
        row_with_blob = _row()
        row_with_blob["blob"] = memoryview(b"PDF")
        cursor = FakeCursor(fetchall_results=[[row_with_blob]])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        result = store.list_by_draft_with_blob(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)
        assert len(result) == 1
        assert isinstance(result[0]["blob"], bytes)
        assert result[0]["blob"] == b"PDF"

    def test_invalid_uuid_returns_empty(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.list_by_draft_with_blob("not-a-uuid", _PROVIDER_DRAFT_ID) == []

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgDraftAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.list_by_draft_with_blob(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)


# ── get ────────────────────────────────────────────────────────────


class TestGetSingle:

    def test_get_returns_row(self, monkeypatch):
        cursor = FakeCursor(fetchone_results=[_row()])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        result = store.get(_DRAFT_ATTACHMENT_ID)
        assert result is not None
        assert result["filename"] == "report.pdf"

    def test_get_returns_none_when_missing(self, monkeypatch):
        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.get(_DRAFT_ATTACHMENT_ID) is None

    def test_get_invalid_uuid_returns_none(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.get("not-a-uuid") is None


# ── delete ─────────────────────────────────────────────────────────


class TestDelete:

    def test_returns_true_when_row_deleted(self, monkeypatch):
        # DELETE ... RETURNING returns the deleted id when a row matched.
        cursor = FakeCursor(fetchone_results=[(_DRAFT_ATTACHMENT_ID,)])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.delete(_DRAFT_ATTACHMENT_ID) is True

    def test_returns_false_when_no_match(self, monkeypatch):
        cursor = FakeCursor()  # fetchone returns None
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.delete(_DRAFT_ATTACHMENT_ID) is False

    def test_invalid_uuid_returns_false(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.delete("not-a-uuid") is False

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.delete(_DRAFT_ATTACHMENT_ID)


# ── update_provider_attachment_id ──────────────────────────────────


class TestUpdateProviderAttachmentId:

    def test_executes_update_with_correct_params(self, monkeypatch):
        # The query now adds RETURNING so the repository can detect a
        # missing row (CASCADE-deleted between read and write). Provide a
        # fetchone result so the code reaches the success path.
        cursor = FakeCursor(fetchone_results=[(_DRAFT_ATTACHMENT_ID,)])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        store.update_provider_attachment_id(_DRAFT_ATTACHMENT_ID, "graph-att-1")
        assert len(cursor.executed) == 1
        _sql, params = cursor.executed[0]
        assert params["draft_attachment_id"] == _DRAFT_ATTACHMENT_ID
        assert params["provider_attachment_id"] == "graph-att-1"

    def test_missing_row_raises_query_error(self, monkeypatch):
        # RETURNING fires zero rows when CASCADE delete races ahead — the
        # repository must surface that loudly so the D-27 partial-success
        # contract does not silently regress to a no-op.
        cursor = FakeCursor(fetchone_results=[None])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.update_provider_attachment_id(_DRAFT_ATTACHMENT_ID, "x")

    def test_invalid_uuid_raises_query_error(self, monkeypatch):
        # Phase 2.2 fix: bad UUID at the boundary now raises instead of
        # returning silently — the D-27 contract relies on writes either
        # succeeding or failing loudly.
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.update_provider_attachment_id("not-a-uuid", "x")

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.update_provider_attachment_id(_DRAFT_ATTACHMENT_ID, "x")


def _stub_execute_values(cur, sql, rows, **kwargs):
    """Stub for psycopg2.extras.execute_values: record the call on the cursor."""
    cur.executed.append((sql, list(rows)))


# ── list_existing_source_attachment_ids (M13, R-12 idempotency) ─────


class TestListExistingSourceAttachmentIds:

    def test_returns_set_of_source_ids_skipping_nulls(self, monkeypatch):
        cursor = FakeCursor(fetchall_results=[[("src-1",), ("src-2",), (None,)]])
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        result = store.list_existing_source_attachment_ids(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)
        assert result == {"src-1", "src-2"}

    def test_invalid_uuid_returns_empty_set(self, monkeypatch):
        cursor = FakeCursor(
            execute_side_effect=psycopg2.errors.InvalidTextRepresentation("bad"),
        )
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        assert store.list_existing_source_attachment_ids("not-a-uuid", _PROVIDER_DRAFT_ID) == set()

    def test_db_error_wrapped(self, monkeypatch):
        cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("down"))
        patch_connection(monkeypatch, repo_module, [cursor])
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.list_existing_source_attachment_ids(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgDraftAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.list_existing_source_attachment_ids(_ACCOUNT_ID, _PROVIDER_DRAFT_ID)


# ── batch_update_provider_attachment_ids (M13, D-27 partial-success) ─


class TestBatchUpdateProviderAttachmentIds:

    def test_empty_input_returns_zero_without_touching_connection(self, monkeypatch):
        # The empty-batch guard must short-circuit before acquiring a
        # connection — patch get_connection to blow up if it is reached.
        patch_connection_error(
            monkeypatch, repo_module, ConnectionPoolError("should not be called"),
        )
        store = PgDraftAttachmentStore()
        assert store.batch_update_provider_attachment_ids([]) == 0

    def test_happy_path_returns_rowcount(self, monkeypatch):
        cursor = FakeCursor(fetchall_results=[[("att-1",), ("att-2",)]])
        patch_connection(monkeypatch, repo_module, [cursor])
        monkeypatch.setattr(psycopg2.extras, "execute_values", _stub_execute_values)
        store = PgDraftAttachmentStore()
        result = store.batch_update_provider_attachment_ids(
            [("att-1", "graph-1"), ("att-2", "graph-2")],
        )
        assert result == 2

    def test_invalid_uuid_raises_query_error(self, monkeypatch):
        def _raise(cur, sql, rows, **kwargs):
            raise psycopg2.errors.InvalidTextRepresentation("bad uuid")

        cursor = FakeCursor()
        patch_connection(monkeypatch, repo_module, [cursor])
        monkeypatch.setattr(psycopg2.extras, "execute_values", _raise)
        store = PgDraftAttachmentStore()
        with pytest.raises(QueryError):
            store.batch_update_provider_attachment_ids([("not-a-uuid", "x")])

    def test_connection_pool_error_propagates(self, monkeypatch):
        patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool"))
        store = PgDraftAttachmentStore()
        with pytest.raises(ConnectionPoolError):
            store.batch_update_provider_attachment_ids([("att-1", "graph-1")])
