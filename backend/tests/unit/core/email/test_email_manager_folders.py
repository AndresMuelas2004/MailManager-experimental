"""Unit tests for the EmailManager folder fan-out (carpetas-y-reglas).

The manager routes each folder operation by client type: Gmail uses opaque
user-label ids (``ensure_user_label`` / ``add_label_to_messages`` / …), Outlook
uses the category NAME as its provider_ref (``add_category_to_message`` /
``remove_category_from_message``) and treats a LABEL-level rename/delete as a
NO-OP (its category displayName is immutable, so the service re-tags each member
instead). Real client instances are registered so the ``isinstance`` routing is
exercised; only their provider methods are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.email.email_manager import EmailManager
from core.email.errors import EmailExternalAPIError
from core.email.gmail_client import GmailClient
from core.email.outlook_client import OutlookClient


_GMAIL_LABEL = "mb__gmail"
_OUTLOOK_LABEL = "mb__outlook"


@pytest.fixture
def gmail(manager: EmailManager) -> GmailClient:
    client = GmailClient(account_label=_GMAIL_LABEL)
    manager.add_client(client)
    return client


@pytest.fixture
def outlook(manager: EmailManager) -> OutlookClient:
    client = OutlookClient(account_label=_OUTLOOK_LABEL)
    manager.add_client(client)
    return client


class TestEnsureFolderRef:
    def test_gmail_delegates_to_ensure_user_label(self, manager, gmail):
        gmail.ensure_user_label = MagicMock(return_value="Label_42")
        assert manager.ensure_folder_ref(_GMAIL_LABEL, "Work") == "Label_42"
        gmail.ensure_user_label.assert_called_once_with("Work")

    def test_outlook_returns_the_name_as_ref(self, manager, outlook):
        # No master list to create — the category NAME is the ref.
        assert manager.ensure_folder_ref(_OUTLOOK_LABEL, "Work") == "Work"

    def test_unexpected_error_wrapped_as_external_api_error(self, manager, gmail):
        gmail.ensure_user_label = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(EmailExternalAPIError):
            manager.ensure_folder_ref(_GMAIL_LABEL, "Work")


class TestAssignFolderToMessage:
    def test_gmail_adds_label_and_keeps_id(self, manager, gmail):
        gmail.add_label_to_messages = MagicMock(return_value=["m1"])
        result = manager.assign_folder_to_message(_GMAIL_LABEL, "m1", "Label_42", "Work")
        assert result == "m1"
        gmail.add_label_to_messages.assert_called_once_with("Label_42", ["m1"])

    def test_outlook_adds_category_and_returns_rewritten_id(self, manager, outlook):
        outlook.add_category_to_message = MagicMock(return_value="rewritten-id")
        result = manager.assign_folder_to_message(_OUTLOOK_LABEL, "m1", "Work", "Work")
        assert result == "rewritten-id"
        outlook.add_category_to_message.assert_called_once_with("m1", "Work")


class TestUnassignFolderFromMessage:
    def test_gmail_removes_label(self, manager, gmail):
        gmail.remove_label_from_messages = MagicMock(return_value=["m1"])
        result = manager.unassign_folder_from_message(_GMAIL_LABEL, "m1", "Label_42", "Work")
        assert result == "m1"
        gmail.remove_label_from_messages.assert_called_once_with("Label_42", ["m1"])

    def test_outlook_removes_category(self, manager, outlook):
        outlook.remove_category_from_message = MagicMock(return_value="m1")
        manager.unassign_folder_from_message(_OUTLOOK_LABEL, "m1", "Work", "Work")
        outlook.remove_category_from_message.assert_called_once_with("m1", "Work")


class TestRenameFolderLabel:
    def test_gmail_patches_label(self, manager, gmail):
        gmail.rename_user_label = MagicMock()
        manager.rename_folder_label(_GMAIL_LABEL, "Label_42", "New name")
        gmail.rename_user_label.assert_called_once_with("Label_42", "New name")

    def test_outlook_is_a_noop(self, manager, outlook):
        # Category displayName is immutable — no label-level rename call exists.
        assert manager.rename_folder_label(_OUTLOOK_LABEL, "Work", "New name") is None


class TestDeleteFolderLabel:
    def test_gmail_deletes_label(self, manager, gmail):
        gmail.delete_user_label = MagicMock()
        manager.delete_folder_label(_GMAIL_LABEL, "Label_42")
        gmail.delete_user_label.assert_called_once_with("Label_42")

    def test_outlook_is_a_noop(self, manager, outlook):
        assert manager.delete_folder_label(_OUTLOOK_LABEL, "Work") is None
