"""Tests espejo de ``services_helpers.persistencia_metadatos``: persistencia y actualizacion de metadatos y cursores de sync."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.errors.exceptions import ApiError, DatabaseQueryError
from api.services.services_helpers import (
    delete_email_metadata_batch,
    load_suspect_message_ids,
    load_sync_cursors,
    persist_email_metadata_batch,
    update_email_metadata_labels_batch,
    update_email_read_status_batch,
    update_email_read_status_by_thread,
    update_email_spam_status_batch,
    update_sync_cursor,
)
from core.email import LabelUpdate, SpamMoveResult
from database import QueryError
from tests.shared.email_fakes import build_metadata


# ------------------------------------------------------------------
# persist_email_metadata_batch
# ------------------------------------------------------------------

class TestPersistEmailMetadataBatch:

    def test_empty_list_returns_zero(self):
        assert persist_email_metadata_batch("acc-1", []) == 0

    def test_happy_path_converts_and_persists(self):
        metadata = [
            build_metadata(provider_message_id="m1"),
            build_metadata(provider_message_id="m2"),
        ]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.upsert_batch.return_value = 2
            result = persist_email_metadata_batch("acc-1", metadata)
        assert result == 2
        call_args = mock_store.upsert_batch.call_args
        assert call_args[0][0] == "acc-1"
        rows = call_args[0][1]
        assert len(rows) == 2
        assert rows[0][0] == "m1"

    def test_database_error_translated(self):
        metadata = [build_metadata()]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.upsert_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                persist_email_metadata_batch("acc-1", metadata)

    def test_generic_exception_raises_api_error(self):
        metadata = [build_metadata()]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.upsert_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to persist email metadata"):
                persist_email_metadata_batch("acc-1", metadata)


# ------------------------------------------------------------------
# delete_email_metadata_batch
# ------------------------------------------------------------------

class TestDeleteEmailMetadataBatch:

    def test_empty_list_returns_zero(self):
        assert delete_email_metadata_batch("acc-1", []) == 0

    def test_happy_path_returns_deleted_count(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.delete_batch_by_message_ids.return_value = 3
            result = delete_email_metadata_batch("acc-1", ["m1", "m2", "m3"])
        assert result == 3

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.delete_batch_by_message_ids.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                delete_email_metadata_batch("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.delete_batch_by_message_ids.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to delete email metadata"):
                delete_email_metadata_batch("acc-1", ["m1"])


# ------------------------------------------------------------------
# update_email_metadata_labels_batch
# ------------------------------------------------------------------

class TestUpdateEmailMetadataLabelsBatch:

    def test_empty_list_returns_zero(self):
        assert update_email_metadata_labels_batch("acc-1", []) == 0

    def test_happy_path_converts_and_updates(self):
        updates = [
            LabelUpdate(provider_message_id="m1", is_read=True, box="INBOX"),
            LabelUpdate(provider_message_id="m2", is_read=False, box="TRASH"),
        ]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.return_value = 2
            result = update_email_metadata_labels_batch("acc-1", updates)
        assert result == 2
        call_args = mock_store.update_labels_batch.call_args
        rows = call_args[0][1]
        assert len(rows) == 2
        assert rows[0] == ("m1", "acc-1", True, "INBOX")

    def test_database_error_translated(self):
        updates = [LabelUpdate(provider_message_id="m1", is_read=True, box="INBOX")]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_metadata_labels_batch("acc-1", updates)

    def test_generic_exception_raises_api_error(self):
        updates = [LabelUpdate(provider_message_id="m1", is_read=True, box="INBOX")]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email metadata labels"):
                update_email_metadata_labels_batch("acc-1", updates)


# ------------------------------------------------------------------
# load_sync_cursors
# ------------------------------------------------------------------

class TestLoadSyncCursors:

    def test_happy_path_returns_cursor_dict(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.return_value = {"acc1": "cursor-123"}
            result = load_sync_cursors(lookup)
        assert result == {"mb__acc1": "cursor-123"}

    def test_batches_one_query_per_mailbox_for_many_accounts(self):
        # M7: N accounts in the same mailbox resolve in a SINGLE batch query,
        # not one query per account.
        lookup = {
            "mb__acc1": ("mb", "acc1", "gmail"),
            "mb__acc2": ("mb", "acc2", "outlook"),
            "mb__acc3": ("mb", "acc3", "gmail"),
        }
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.return_value = {
                "acc1": "c1", "acc2": None, "acc3": "c3",
            }
            result = load_sync_cursors(lookup)
        assert mock_store.get_sync_cursors_for_mailbox.call_count == 1
        assert result == {"mb__acc1": "c1", "mb__acc2": None, "mb__acc3": "c3"}

    def test_database_error_translated(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_sync_cursors(lookup)

    def test_generic_exception_raises_api_error(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load sync cursor"):
                load_sync_cursors(lookup)


# ------------------------------------------------------------------
# update_sync_cursor
# ------------------------------------------------------------------

class TestUpdateSyncCursor:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            update_sync_cursor("mb-1", "acc-1", "cursor-new")
        mock_store.update_sync_cursor.assert_called_once_with("mb-1", "acc-1", "cursor-new")

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.update_sync_cursor.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_sync_cursor("mb-1", "acc-1", "cursor-new")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.account_store") as mock_store:
            mock_store.update_sync_cursor.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update sync cursor"):
                update_sync_cursor("mb-1", "acc-1", "cursor-new")


# ------------------------------------------------------------------
# update_email_read_status_batch
# ------------------------------------------------------------------

class TestUpdateEmailReadStatusBatch:

    def test_empty_returns_zero(self):
        assert update_email_read_status_batch("acc-1", [], True) == 0

    def test_happy_path_returns_updated_count(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_batch.return_value = 3
            result = update_email_read_status_batch("acc-1", ["m1", "m2", "m3"], True)
        assert result == 3
        call_args = mock_store.update_read_status_batch.call_args
        assert call_args[0][0] == "acc-1"
        rows = call_args[0][1]
        assert len(rows) == 3
        assert rows[0] == ("m1", "acc-1", True)

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_read_status_batch("acc-1", ["m1"], False)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email read status"):
                update_email_read_status_batch("acc-1", ["m1"], True)


# ------------------------------------------------------------------
# update_email_read_status_by_thread
# ------------------------------------------------------------------

class TestUpdateEmailReadStatusByThread:

    def test_empty_returns_zero(self):
        assert update_email_read_status_by_thread("acc-1", [], True) == 0

    def test_happy_path_returns_updated_count(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_by_thread.return_value = 4
            result = update_email_read_status_by_thread("acc-1", ["m1"], True)
        assert result == 4
        call_args = mock_store.update_read_status_by_thread.call_args
        assert call_args[0][0] == "acc-1"
        assert call_args[0][1] == ["m1"]
        assert call_args[0][2] is True

    def test_query_error_translates_to_database_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_by_thread.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_read_status_by_thread("acc-1", ["m1"], False)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_read_status_by_thread.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email read status by thread"):
                update_email_read_status_by_thread("acc-1", ["m1"], True)


# ------------------------------------------------------------------
# update_email_spam_status_batch
# ------------------------------------------------------------------

class TestUpdateEmailSpamStatusBatch:

    def test_empty_returns_zero(self):
        assert update_email_spam_status_batch("acc-1", [], "SPAM") == 0

    def test_happy_path_returns_updated_count(self):
        results = [
            SpamMoveResult(old_id="old_m1", new_id="new_m1"),
            SpamMoveResult(old_id="old_m2", new_id="new_m2"),
        ]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_spam_status_batch.return_value = 2
            count = update_email_spam_status_batch("acc-1", results, "SPAM")
        assert count == 2
        call_args = mock_store.update_spam_status_batch.call_args
        assert call_args[0][0] == "acc-1"
        rows = call_args[0][1]
        assert len(rows) == 2
        assert rows[0] == ("old_m1", "acc-1", "new_m1", "SPAM")

    def test_database_error_translated(self):
        results = [SpamMoveResult(old_id="old_m1", new_id="new_m1")]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_spam_status_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_spam_status_batch("acc-1", results, "SPAM")

    def test_generic_exception_raises_api_error(self):
        results = [SpamMoveResult(old_id="old_m1", new_id="new_m1")]
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.update_spam_status_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email spam status"):
                update_email_spam_status_batch("acc-1", results, "SPAM")


# ------------------------------------------------------------------
# load_suspect_message_ids
# ------------------------------------------------------------------

class TestLoadSuspectMessageIds:

    def test_happy_path_returns_suspect_ids(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.return_value = ["ghost1"]
            result = load_suspect_message_ids("acc-1", ["boot1", "boot2"])
        assert result == ["ghost1"]
        mock_store.list_provider_message_ids_not_in.assert_called_once_with(
            "acc-1", ["boot1", "boot2"],
        )

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_suspect_message_ids("acc-1", [])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.persistencia_metadatos.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load suspect message IDs"):
                load_suspect_message_ids("acc-1", [])
