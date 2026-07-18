"""Tests espejo de ``gmail_client.etiquetas`` (user labels = MISSELA folders)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.email.errors import EmailNotAuthenticatedError, LabelOperationError
from core.email.gmail_client import GmailClient


def _http_error(status: int) -> Exception:
    from googleapiclient.errors import HttpError

    resp = MagicMock()
    type(resp).status = status
    return HttpError(resp=resp, content=b"err")


def _labels_execute(client: GmailClient) -> MagicMock:
    return client.service.users.return_value.labels.return_value


class TestEnsureUserLabel:
    def test_guard_requires_authentication(self, client: GmailClient):
        assert client.service is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.ensure_user_label("Work")

    def test_adopts_existing_user_label_case_insensitively(self, client: GmailClient):
        client.service = MagicMock()
        labels = _labels_execute(client)
        labels.list.return_value.execute.return_value = {
            "labels": [
                {"type": "system", "name": "INBOX", "id": "INBOX"},
                {"type": "user", "name": "work", "id": "Label_5"},
            ]
        }
        assert client.ensure_user_label("Work") == "Label_5"
        # Adoption: an existing label is reused, never re-created.
        labels.create.assert_not_called()

    def test_creates_label_when_absent(self, client: GmailClient):
        client.service = MagicMock()
        labels = _labels_execute(client)
        labels.list.return_value.execute.return_value = {"labels": []}
        labels.create.return_value.execute.return_value = {"id": "Label_new"}
        assert client.ensure_user_label("Work") == "Label_new"
        labels.create.assert_called_once()

    def test_list_http_error_becomes_label_operation_error(self, client: GmailClient):
        client.service = MagicMock()
        _labels_execute(client).list.return_value.execute.side_effect = _http_error(500)
        with pytest.raises(LabelOperationError):
            client.ensure_user_label("Work")


class TestRenameUserLabel:
    def test_guard_requires_authentication(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.rename_user_label("Label_5", "New")

    def test_patches_label(self, client: GmailClient):
        client.service = MagicMock()
        labels = _labels_execute(client)
        client.rename_user_label("Label_5", "New")
        labels.patch.assert_called_once()
        assert labels.patch.call_args.kwargs["id"] == "Label_5"
        assert labels.patch.call_args.kwargs["body"] == {"name": "New"}

    def test_http_error_becomes_label_operation_error(self, client: GmailClient):
        client.service = MagicMock()
        _labels_execute(client).patch.return_value.execute.side_effect = _http_error(500)
        with pytest.raises(LabelOperationError):
            client.rename_user_label("Label_5", "New")


class TestDeleteUserLabel:
    def test_deletes_label(self, client: GmailClient):
        client.service = MagicMock()
        labels = _labels_execute(client)
        client.delete_user_label("Label_5")
        labels.delete.assert_called_once()

    def test_404_is_idempotent_success(self, client: GmailClient):
        client.service = MagicMock()
        _labels_execute(client).delete.return_value.execute.side_effect = _http_error(404)
        # An already-gone label is treated as success so delete stays idempotent.
        assert client.delete_user_label("Label_5") is None

    def test_other_http_error_becomes_label_operation_error(self, client: GmailClient):
        client.service = MagicMock()
        _labels_execute(client).delete.return_value.execute.side_effect = _http_error(500)
        with pytest.raises(LabelOperationError):
            client.delete_user_label("Label_5")


class TestApplyLabelToMessages:
    def test_add_guard_requires_authentication(self, client: GmailClient):
        with pytest.raises(EmailNotAuthenticatedError):
            client.add_label_to_messages("Label_5", ["m1"])

    def test_add_empty_returns_empty_without_call(self, client: GmailClient):
        client.service = MagicMock()
        client._batch_modify_labels = MagicMock()
        assert client.add_label_to_messages("Label_5", []) == []
        client._batch_modify_labels.assert_not_called()

    def test_add_delegates_to_batch_modify(self, client: GmailClient):
        client.service = MagicMock()
        client._batch_modify_labels = MagicMock(return_value=["m1"])
        assert client.add_label_to_messages("Label_5", ["m1"]) == ["m1"]
        client._batch_modify_labels.assert_called_once_with(["m1"], add_labels=["Label_5"])

    def test_remove_delegates_to_batch_modify(self, client: GmailClient):
        client.service = MagicMock()
        client._batch_modify_labels = MagicMock(return_value=["m1"])
        assert client.remove_label_from_messages("Label_5", ["m1"]) == ["m1"]
        client._batch_modify_labels.assert_called_once_with(["m1"], remove_labels=["Label_5"])
