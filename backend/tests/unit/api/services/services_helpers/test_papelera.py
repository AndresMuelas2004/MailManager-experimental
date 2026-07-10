"""Tests espejo de ``services_helpers.papelera``: mover a papelera, marcar borrado y restaurar."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.errors.exceptions import ApiError, DatabaseQueryError
from api.services.services_helpers import (
    get_trash_emails_by_ids,
    mark_as_deleted_batch,
    move_to_trash_batch,
    restore_from_trash_batch,
    restore_from_trash_discovered_batch,
)
from database import QueryError


# ------------------------------------------------------------------
# get_trash_emails_by_ids
# ------------------------------------------------------------------

class TestGetTrashEmailsByIds:

    def test_empty_list_returns_empty(self):
        assert get_trash_emails_by_ids("acc-1", []) == []

    def test_happy_path_returns_rows(self):
        fake_rows = [
            {"provider_message_id": "m1", "box": "TRASH", "previous_box": "ALL_MAIL"},
        ]
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.get_trash_emails_by_ids.return_value = fake_rows
            result = get_trash_emails_by_ids("acc-1", ["m1"])
        assert result == fake_rows

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.get_trash_emails_by_ids.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                get_trash_emails_by_ids("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.get_trash_emails_by_ids.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to get trash emails"):
                get_trash_emails_by_ids("acc-1", ["m1"])


# ------------------------------------------------------------------
# mark_as_deleted_batch
# ------------------------------------------------------------------

class TestMarkAsDeletedBatch:

    def test_empty_list_returns_zero(self):
        assert mark_as_deleted_batch("acc-1", []) == 0

    def test_happy_path_returns_count(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.mark_as_deleted_batch.return_value = 2
            result = mark_as_deleted_batch("acc-1", ["m1", "m2"])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.mark_as_deleted_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                mark_as_deleted_batch("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.mark_as_deleted_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to mark emails as deleted"):
                mark_as_deleted_batch("acc-1", ["m1"])


# ------------------------------------------------------------------
# restore_from_trash_batch
# ------------------------------------------------------------------

class TestRestoreFromTrashBatch:

    def test_empty_list_returns_zero(self):
        assert restore_from_trash_batch("acc-1", []) == 0

    def test_happy_path_returns_count(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_batch.return_value = 2
            result = restore_from_trash_batch("acc-1", [("m1", "m1", "acc-1"), ("m2", "m2", "acc-1")])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                restore_from_trash_batch("acc-1", [("m1", "m1", "acc-1")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to restore emails from trash"):
                restore_from_trash_batch("acc-1", [("m1", "m1", "acc-1")])


# ------------------------------------------------------------------
# restore_from_trash_discovered_batch
# ------------------------------------------------------------------

class TestRestoreFromTrashDiscoveredBatch:

    def test_empty_list_returns_zero(self):
        assert restore_from_trash_discovered_batch("acc-1", []) == 0

    def test_happy_path_returns_count(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_discovered_batch.return_value = 2
            result = restore_from_trash_discovered_batch(
                "acc-1", [("m1", "m1", "acc-1", "SENT"), ("m2", "m2", "acc-1", "SPAM")],
            )
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_discovered_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                restore_from_trash_discovered_batch("acc-1", [("m1", "m1", "acc-1", "SENT")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_discovered_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to restore emails with discovered box"):
                restore_from_trash_discovered_batch("acc-1", [("m1", "m1", "acc-1", "SENT")])


# ------------------------------------------------------------------
# move_to_trash_batch
# ------------------------------------------------------------------

class TestMoveToTrashBatch:

    def test_empty_list_returns_zero(self):
        assert move_to_trash_batch("acc-1", []) == 0

    def test_happy_path_returns_count(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.return_value = 2
            result = move_to_trash_batch("acc-1", [("m1", "m1", "acc-1"), ("m2", "m2", "acc-1")])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                move_to_trash_batch("acc-1", [("m1", "m1", "acc-1")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.papelera.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to move emails to trash"):
                move_to_trash_batch("acc-1", [("m1", "m1", "acc-1")])
