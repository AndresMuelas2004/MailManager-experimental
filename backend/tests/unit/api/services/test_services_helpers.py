"""
Unit tests for services_helpers.
"""

from __future__ import annotations

import logging

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountMisconfigured,
    AccountNotConnected,
    ApiError,
    CredentialFileError,
    DatabaseQueryError,
    ExternalAPIError,
    Forbidden,
    MailboxNotFound,
)
from api.schemas.email import AccountSyncFailure
from api.services.services_helpers import (
    _wrap_secret,
    build_account_sync_failures,
    build_manager_for_accounts,
    delete_email_metadata_batch,
    ensure_mailbox_access,
    get_email_content,
    get_trash_emails_by_ids,
    is_auth_error,
    list_unread_recent_uncached,
    load_suspect_message_ids,
    load_sync_cursors,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    mark_as_deleted_batch,
    move_to_trash_batch,
    parse_search_query,
    parse_search_tokens,
    persist_email_content,
    persist_email_metadata_batch,
    purge_expired_email_content,
    raise_on_silent_auth_errors,
    restore_from_trash_batch,
    restore_from_trash_discovered_batch,
    row_to_email_metadata_out,
    touch_email_content_last_accessed,
    translate_connect_error,
    unwrap_secret,
    update_email_metadata_labels_batch,
    update_email_read_status_batch,
    update_email_read_status_by_thread,
    update_email_spam_status_batch,
    update_sync_cursor,
)
from core.email import LabelUpdate, SpamMoveResult
from core.email.errors import (
    CoreError,
    EmailAuthError,
    EmailExternalAPIError,
    EmailMissingTokenError,
)
from database import CredentialReadError, QueryError
from tests.shared.email_fakes import build_metadata


class TestEnsureMailboxAccess:

    def test_null_owner_raises_forbidden(self):
        """A mailbox with owner_user_id=None must be rejected."""
        fake_record = {
            "mailbox_id": "mb-1",
            "display_name": "Orphan",
            "owner_user_id": None,
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.return_value = fake_record
            with pytest.raises(Forbidden):
                ensure_mailbox_access("mb-1", "some-user-id")

    def test_mismatched_owner_raises_forbidden(self):
        """A mailbox owned by a different user must be rejected."""
        fake_record = {
            "mailbox_id": "mb-1",
            "display_name": "Other's MB",
            "owner_user_id": "owner-a",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.return_value = fake_record
            with pytest.raises(Forbidden):
                ensure_mailbox_access("mb-1", "owner-b")

    def test_matching_owner_returns_record(self):
        """A mailbox owned by the requesting user is returned."""
        fake_record = {
            "mailbox_id": "mb-1",
            "display_name": "My MB",
            "owner_user_id": "owner-a",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.return_value = fake_record
            result = ensure_mailbox_access("mb-1", "owner-a")
        assert result == fake_record

    def test_database_error_translated(self):
        """QueryError from mailbox_store.get → DatabaseQueryError."""
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                ensure_mailbox_access("mb-1", "some-user-id")

    def test_generic_exception_raises_api_error(self):
        """RuntimeError from mailbox_store.get → ApiError fallback."""
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to look up mailbox"):
                ensure_mailbox_access("mb-1", "some-user-id")


# ------------------------------------------------------------------
# build_manager_for_accounts — except Exception fallback
# ------------------------------------------------------------------

class TestBuildManagerUnexpectedException:

    def test_unexpected_exception_raises_account_misconfigured(self):
        """A non-CoreError from add_account_record → AccountMisconfigured."""
        account = {"mailbox_id": "mb-1", "account_id": "acc-1", "provider": "gmail"}
        with patch(
            "api.services.services_helpers.EmailManager"
        ) as mock_manager_cls:
            mock_manager_cls.return_value.add_account_record.side_effect = (
                RuntimeError("unexpected boom")
            )
            with pytest.raises(AccountMisconfigured, match="Failed to register account"):
                build_manager_for_accounts([account])


# ------------------------------------------------------------------
# ensure_mailbox_access — store returns None
# ------------------------------------------------------------------

class TestEnsureMailboxAccessNotFound:

    def test_mailbox_not_found_when_store_returns_none(self):
        with patch("api.services.services_helpers.mailbox_store") as mock_store:
            mock_store.get.return_value = None
            with pytest.raises(MailboxNotFound, match="not found"):
                ensure_mailbox_access("mb-1", "some-user-id")


# ------------------------------------------------------------------
# raise_on_silent_auth_errors
# ------------------------------------------------------------------

class TestRaiseOnSilentAuthErrors:

    def test_empty_errors_returns_none(self):
        assert raise_on_silent_auth_errors({}) is None

    def test_single_auth_error_raises_account_not_connected(self):
        errors = {"mb__acc1": EmailAuthError("token expired")}
        with pytest.raises(AccountNotConnected) as exc_info:
            raise_on_silent_auth_errors(errors)
        assert "mb__acc1" in exc_info.value.detail["account_labels"]

    def test_multiple_auth_errors_aggregated(self):
        errors = {
            "mb__acc1": EmailAuthError("expired"),
            "mb__acc2": EmailMissingTokenError("missing"),
        }
        with pytest.raises(AccountNotConnected) as exc_info:
            raise_on_silent_auth_errors(errors)
        labels = exc_info.value.detail["account_labels"]
        assert "mb__acc1" in labels
        assert "mb__acc2" in labels

    def test_non_auth_core_error_translated_and_raised(self):
        errors = {"mb__acc1": EmailExternalAPIError("API fail")}
        with pytest.raises(ExternalAPIError):
            raise_on_silent_auth_errors(errors)

    def test_non_core_error_raises_fallback_api_error(self):
        errors = {"mb__acc1": RuntimeError("something")}
        with pytest.raises(ApiError):
            raise_on_silent_auth_errors(errors)

    def test_reasons_included_in_detail(self):
        errors = {"mb__acc1": EmailAuthError("token expired")}
        with pytest.raises(AccountNotConnected) as exc_info:
            raise_on_silent_auth_errors(errors)
        assert "reasons" in exc_info.value.detail
        assert exc_info.value.detail["reasons"]["mb__acc1"] == "token expired"


# ------------------------------------------------------------------
# build_account_sync_failures — the non-raising counterpart used on the
# partial-success path of sync_email_metadata.
# ------------------------------------------------------------------

class TestBuildAccountSyncFailures:

    def test_empty_errors_returns_empty_list(self):
        assert build_account_sync_failures({}, {}) == []

    @pytest.mark.parametrize(
        "error, expected_reason",
        [
            (EmailAuthError("token revoked"), "account_not_connected"),
            (EmailMissingTokenError("missing"), "account_not_connected"),
            (EmailExternalAPIError("provider 500"), "sync_failed"),
            (RuntimeError("boom"), "sync_failed"),
        ],
    )
    def test_reason_is_decided_by_is_auth_error(self, error, expected_reason):
        # Only a typed auth error maps to ``account_not_connected``; every
        # other per-account failure (core or not) is ``sync_failed``.
        failures = build_account_sync_failures(
            {"mb__acc1": error},
            {"mb__acc1": ("mb", "acc1", "gmail")},
        )
        assert len(failures) == 1
        assert failures[0].reason == expected_reason

    def test_row_carries_account_id_and_provider_from_lookup(self):
        failures = build_account_sync_failures(
            {"mb__acc2": EmailAuthError("expired")},
            {"mb__acc2": ("mb", "acc2", "outlook")},
        )
        assert failures == [
            AccountSyncFailure(
                account_id="acc2", provider="outlook",
                reason="account_not_connected",
            )
        ]

    def test_labels_absent_from_lookup_are_skipped(self):
        # A label present in the error map but missing from ``label_lookup``
        # (mirrors the sync loop's own ``label_lookup.get`` guard) is dropped,
        # never emitted with placeholder ids.
        failures = build_account_sync_failures(
            {
                "mb__acc1": EmailAuthError("expired"),
                "mb__orphan": RuntimeError("no lookup entry"),
            },
            {"mb__acc1": ("mb", "acc1", "gmail")},
        )
        assert [f.account_id for f in failures] == ["acc1"]

    def test_logs_each_failure_with_cause_chain(self, caplog):
        """The reduction to bounded categories is the only observability point
        for these failures (a partial-success sync responds 200 and never
        reaches the ApiError handler): auth failures log at WARNING, the rest
        at ERROR, both carrying the exception's traceback via exc_info."""
        try:
            raise RuntimeError("db exploded")
        except RuntimeError as exc:
            non_auth_error = exc
        with caplog.at_level(logging.WARNING):
            build_account_sync_failures(
                {
                    "mb__acc1": EmailAuthError("token revoked"),
                    "mb__acc2": non_auth_error,
                },
                {
                    "mb__acc1": ("mb", "acc1", "gmail"),
                    "mb__acc2": ("mb", "acc2", "outlook"),
                },
            )
        records = [r for r in caplog.records if "failed_accounts" in r.getMessage()]
        assert len(records) == 2
        by_account = {
            ("acc1" if "acc1" in r.getMessage() else "acc2"): r for r in records
        }
        assert by_account["acc1"].levelno == logging.WARNING
        assert by_account["acc2"].levelno == logging.ERROR
        # The rendered log output carries the swallowed exception's detail.
        assert "db exploded" in caplog.text

    def test_preserves_all_known_failures(self):
        errors = {
            "mb__acc1": EmailAuthError("token revoked"),
            "mb__acc2": EmailExternalAPIError("provider 500"),
        }
        label_lookup = {
            "mb__acc1": ("mb", "acc1", "gmail"),
            "mb__acc2": ("mb", "acc2", "outlook"),
        }
        failures = build_account_sync_failures(errors, label_lookup)
        assert [(f.account_id, f.reason) for f in failures] == [
            ("acc1", "account_not_connected"),
            ("acc2", "sync_failed"),
        ]


# ------------------------------------------------------------------
# translate_connect_error
# ------------------------------------------------------------------

class TestTranslateConnectError:

    def test_email_auth_error_returns_account_connect_auth_error(self):
        exc = EmailAuthError("Token rejected.")
        result = translate_connect_error(exc)
        assert isinstance(result, AccountConnectAuthError)
        assert result.detail.get("core_code") == EmailAuthError.code

    def test_other_core_error_uses_standard_mapping(self):
        exc = EmailExternalAPIError("API fail")
        result = translate_connect_error(exc)
        assert isinstance(result, ExternalAPIError)

    def test_non_core_error_uses_fallback(self):
        exc = RuntimeError("unexpected")
        result = translate_connect_error(exc)
        assert isinstance(result, AccountConnectAuthError)


# ------------------------------------------------------------------
# load_wrapped_app_credentials
# ------------------------------------------------------------------

class TestLoadWrappedAppCredentials:

    def test_happy_path_wraps_client_secret(self):
        fake_creds = {"client_id": "cid", "client_secret": "secret"}
        with patch("api.services.services_helpers.load_app_credentials", return_value=fake_creds):
            result = load_wrapped_app_credentials("gmail")
        assert isinstance(result["client_secret"], SecretStr)
        assert result["client_secret"].get_secret_value() == "secret"
        assert result["client_id"] == "cid"

    def test_database_error_translated(self):
        with patch(
            "api.services.services_helpers.load_app_credentials",
            side_effect=CredentialReadError("read fail"),
        ):
            with pytest.raises(CredentialFileError):
                load_wrapped_app_credentials("gmail")

    def test_generic_exception_raises_api_error(self):
        with patch(
            "api.services.services_helpers.load_app_credentials",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ApiError, match="Failed to load app credentials"):
                load_wrapped_app_credentials("gmail")


# ------------------------------------------------------------------
# load_wrapped_account_tokens
# ------------------------------------------------------------------

class TestLoadWrappedAccountTokens:

    def test_happy_path_wraps_tokens(self):
        fake_tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_tokens.return_value = fake_tokens
            result = load_wrapped_account_tokens("mb-1", "acc-1", "gmail")
        assert isinstance(result["access_token"], SecretStr)
        assert isinstance(result["refresh_token"], SecretStr)
        assert result["expires_in"] == 3600

    def test_none_returns_empty_dict(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_tokens.return_value = None
            result = load_wrapped_account_tokens("mb-1", "acc-1", "gmail")
        assert result == {}

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_tokens.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_wrapped_account_tokens("mb-1", "acc-1", "gmail")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_tokens.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load account tokens"):
                load_wrapped_account_tokens("mb-1", "acc-1", "gmail")


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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.upsert_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                persist_email_metadata_batch("acc-1", metadata)

    def test_generic_exception_raises_api_error(self):
        metadata = [build_metadata()]
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.delete_batch_by_message_ids.return_value = 3
            result = delete_email_metadata_batch("acc-1", ["m1", "m2", "m3"])
        assert result == 3

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.delete_batch_by_message_ids.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                delete_email_metadata_batch("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.return_value = 2
            result = update_email_metadata_labels_batch("acc-1", updates)
        assert result == 2
        call_args = mock_store.update_labels_batch.call_args
        rows = call_args[0][1]
        assert len(rows) == 2
        assert rows[0] == ("m1", "acc-1", True, "INBOX")

    def test_database_error_translated(self):
        updates = [LabelUpdate(provider_message_id="m1", is_read=True, box="INBOX")]
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_metadata_labels_batch("acc-1", updates)

    def test_generic_exception_raises_api_error(self):
        updates = [LabelUpdate(provider_message_id="m1", is_read=True, box="INBOX")]
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_labels_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email metadata labels"):
                update_email_metadata_labels_batch("acc-1", updates)


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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.get_trash_emails_by_ids.return_value = fake_rows
            result = get_trash_emails_by_ids("acc-1", ["m1"])
        assert result == fake_rows

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.get_trash_emails_by_ids.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                get_trash_emails_by_ids("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.mark_as_deleted_batch.return_value = 2
            result = mark_as_deleted_batch("acc-1", ["m1", "m2"])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.mark_as_deleted_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                mark_as_deleted_batch("acc-1", ["m1"])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_batch.return_value = 2
            result = restore_from_trash_batch("acc-1", [("m1", "m1", "acc-1"), ("m2", "m2", "acc-1")])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                restore_from_trash_batch("acc-1", [("m1", "m1", "acc-1")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_discovered_batch.return_value = 2
            result = restore_from_trash_discovered_batch(
                "acc-1", [("m1", "m1", "acc-1", "SENT"), ("m2", "m2", "acc-1", "SPAM")],
            )
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.restore_from_trash_discovered_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                restore_from_trash_discovered_batch("acc-1", [("m1", "m1", "acc-1", "SENT")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.return_value = 2
            result = move_to_trash_batch("acc-1", [("m1", "m1", "acc-1"), ("m2", "m2", "acc-1")])
        assert result == 2

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                move_to_trash_batch("acc-1", [("m1", "m1", "acc-1")])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.move_to_trash_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to move emails to trash"):
                move_to_trash_batch("acc-1", [("m1", "m1", "acc-1")])


# ------------------------------------------------------------------
# load_sync_cursors
# ------------------------------------------------------------------

class TestLoadSyncCursors:

    def test_happy_path_returns_cursor_dict(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.account_store") as mock_store:
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
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.return_value = {
                "acc1": "c1", "acc2": None, "acc3": "c3",
            }
            result = load_sync_cursors(lookup)
        assert mock_store.get_sync_cursors_for_mailbox.call_count == 1
        assert result == {"mb__acc1": "c1", "mb__acc2": None, "mb__acc3": "c3"}

    def test_database_error_translated(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_sync_cursors(lookup)

    def test_generic_exception_raises_api_error(self):
        lookup = {"mb__acc1": ("mb", "acc1", "gmail")}
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.get_sync_cursors_for_mailbox.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load sync cursor"):
                load_sync_cursors(lookup)


# ------------------------------------------------------------------
# update_sync_cursor
# ------------------------------------------------------------------

class TestUpdateSyncCursor:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
            update_sync_cursor("mb-1", "acc-1", "cursor-new")
        mock_store.update_sync_cursor.assert_called_once_with("mb-1", "acc-1", "cursor-new")

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
            mock_store.update_sync_cursor.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_sync_cursor("mb-1", "acc-1", "cursor-new")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.account_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_read_status_batch.return_value = 3
            result = update_email_read_status_batch("acc-1", ["m1", "m2", "m3"], True)
        assert result == 3
        call_args = mock_store.update_read_status_batch.call_args
        assert call_args[0][0] == "acc-1"
        rows = call_args[0][1]
        assert len(rows) == 3
        assert rows[0] == ("m1", "acc-1", True)

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_read_status_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_read_status_batch("acc-1", ["m1"], False)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_read_status_by_thread.return_value = 4
            result = update_email_read_status_by_thread("acc-1", ["m1"], True)
        assert result == 4
        call_args = mock_store.update_read_status_by_thread.call_args
        assert call_args[0][0] == "acc-1"
        assert call_args[0][1] == ["m1"]
        assert call_args[0][2] is True

    def test_query_error_translates_to_database_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_read_status_by_thread.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_read_status_by_thread("acc-1", ["m1"], False)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
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
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_spam_status_batch.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                update_email_spam_status_batch("acc-1", results, "SPAM")

    def test_generic_exception_raises_api_error(self):
        results = [SpamMoveResult(old_id="old_m1", new_id="new_m1")]
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.update_spam_status_batch.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to update email spam status"):
                update_email_spam_status_batch("acc-1", results, "SPAM")


# ------------------------------------------------------------------
# load_suspect_message_ids
# ------------------------------------------------------------------

class TestLoadSuspectMessageIds:

    def test_happy_path_returns_suspect_ids(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.return_value = ["ghost1"]
            result = load_suspect_message_ids("acc-1", ["boot1", "boot2"])
        assert result == ["ghost1"]
        mock_store.list_provider_message_ids_not_in.assert_called_once_with(
            "acc-1", ["boot1", "boot2"],
        )

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_suspect_message_ids("acc-1", [])

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_provider_message_ids_not_in.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load suspect message IDs"):
                load_suspect_message_ids("acc-1", [])


# ------------------------------------------------------------------
# row_to_email_metadata_out — thread_message_count projection
# ------------------------------------------------------------------


def _metadata_row(**overrides):
    base = {
        "provider_message_id": "m1",
        "account_id": "acc-1",
        "mailbox_id": "mb-1",
        "thread_id": "t1",
        "from_email": "a@b.com",
        "from_name": "A",
        "subject": "s",
        "received_at": "2026-01-01T00:00:00+00:00",
        "is_read": False,
        "box": "ALL_MAIL",
    }
    base.update(overrides)
    return base


class TestRowToEmailMetadataOut:

    def test_populates_thread_message_count_from_row(self):
        out = row_to_email_metadata_out(_metadata_row(thread_message_count=4))
        assert out.thread_message_count == 4

    def test_thread_message_count_defaults_to_one_when_absent(self):
        # Non-grouped listings (Favourites) and each message inside a
        # ConversationOut omit the key → must fall back to 1, never 0.
        out = row_to_email_metadata_out(_metadata_row())
        assert out.thread_message_count == 1

    def test_thread_message_count_falsy_value_falls_back_to_one(self):
        # A NULL / 0 thread_message_count would be nonsensical for a row that
        # represents at least itself; the helper coalesces it to 1.
        out = row_to_email_metadata_out(_metadata_row(thread_message_count=None))
        assert out.thread_message_count == 1


# ------------------------------------------------------------------
# is_auth_error
# ------------------------------------------------------------------

class TestIsAuthError:

    def test_true_for_email_auth_error(self):
        exc = EmailAuthError("token expired")
        assert is_auth_error(exc) is True

    def test_false_for_other_core_error(self):
        exc = EmailExternalAPIError("API fail")
        assert is_auth_error(exc) is False

    def test_false_for_non_core_error(self):
        exc = RuntimeError("something")
        assert is_auth_error(exc) is False


# ------------------------------------------------------------------
# unwrap_secret
# ------------------------------------------------------------------

class TestUnwrapSecret:

    def test_none_returns_none(self):
        assert unwrap_secret(None) is None

    def test_secret_str_returns_unwrapped(self):
        secret = SecretStr("my-secret")
        assert unwrap_secret(secret) == "my-secret"

    def test_plain_value_returns_plain(self):
        assert unwrap_secret("plain-value") == "plain-value"


# ------------------------------------------------------------------
# _wrap_secret
# ------------------------------------------------------------------

class TestWrapSecret:

    def test_none_returns_none(self):
        assert _wrap_secret(None) is None

    def test_value_returns_secret_str(self):
        result = _wrap_secret("my-value")
        assert isinstance(result, SecretStr)
        assert result.get_secret_value() == "my-value"


# ------------------------------------------------------------------
# get_email_content
# ------------------------------------------------------------------

class TestGetEmailContent:

    def test_happy_path_returns_dict(self):
        fake_row = {"html_body": "<p>hi</p>", "text_body": "hi"}
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.get.return_value = fake_row
            result = get_email_content("acc-1", "m1")
        assert result == fake_row

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.get.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                get_email_content("acc-1", "m1")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.get.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to read email content"):
                get_email_content("acc-1", "m1")


# ------------------------------------------------------------------
# persist_email_content
# ------------------------------------------------------------------

class TestPersistEmailContent:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            persist_email_content("acc-1", "m1", "<p>hi</p>", "hi")
        mock_store.upsert.assert_called_once_with("acc-1", "m1", "<p>hi</p>", "hi")

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.upsert.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                persist_email_content("acc-1", "m1", None, None)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.upsert.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to persist email content"):
                persist_email_content("acc-1", "m1", None, None)


# ------------------------------------------------------------------
# touch_email_content_last_accessed (sliding TTL refresh on cache hit)
# ------------------------------------------------------------------
# Best-effort by design: a failure to bump the TTL must NEVER affect the
# content response — the body is already served from cache. So unlike the
# translation helpers below, EVERY error is swallowed (logged, not raised).

class TestTouchEmailContentLastAccessed:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            touch_email_content_last_accessed("acc-1", "m1")
        mock_store.touch_last_accessed.assert_called_once_with("acc-1", "m1")

    def test_database_error_swallowed(self):
        # A DatabaseError must NOT propagate — the read already succeeded.
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.touch_last_accessed.side_effect = QueryError("DB fail")
            # No exception escapes.
            assert touch_email_content_last_accessed("acc-1", "m1") is None

    def test_generic_exception_swallowed(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.touch_last_accessed.side_effect = RuntimeError("boom")
            assert touch_email_content_last_accessed("acc-1", "m1") is None


# ------------------------------------------------------------------
# purge_expired_email_content (post-sync TTL eviction, best-effort)
# ------------------------------------------------------------------
# Runs in the post-sync background task and must never raise. Returns the
# rowcount on success, or 0 on any error (the next sync retries).

class TestPurgeExpiredEmailContent:

    def test_happy_path_returns_rowcount(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.return_value = 4
            result = purge_expired_email_content(["acc-1", "acc-2"])
        assert result == 4
        mock_store.purge_expired_for_accounts.assert_called_once_with(["acc-1", "acc-2"])

    def test_database_error_returns_zero(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.side_effect = QueryError("DB fail")
            assert purge_expired_email_content(["acc-1"]) == 0

    def test_generic_exception_returns_zero(self):
        with patch("api.services.services_helpers.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.side_effect = RuntimeError("boom")
            assert purge_expired_email_content(["acc-1"]) == 0


# ------------------------------------------------------------------
# list_unread_recent_uncached (content-prefetch target selection)
# ------------------------------------------------------------------
# Thin translation wrapper that CAN raise (the prefetch caller wraps it in
# its own best-effort try/except), so it follows the standard translation
# pattern rather than swallowing.

class TestListUnreadRecentUncached:

    def test_happy_path_returns_ids(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.return_value = ["m3", "m1"]
            result = list_unread_recent_uncached("acc-1", 50)
        assert result == ["m3", "m1"]
        mock_store.list_unread_recent_uncached.assert_called_once_with("acc-1", 50)

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                list_unread_recent_uncached("acc-1", 50)

    def test_generic_exception_raises_fallback(self):
        with patch("api.services.services_helpers.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to list unread recent uncached messages"):
                list_unread_recent_uncached("acc-1", 50)


# ------------------------------------------------------------------
# parse_search_tokens
# ------------------------------------------------------------------

class TestParseSearchTokens:

    def test_none_returns_empty_list(self):
        assert parse_search_tokens(None) == []

    def test_empty_string_returns_empty_list(self):
        assert parse_search_tokens("") == []

    def test_only_whitespace_returns_empty_list(self):
        assert parse_search_tokens("   ") == []
        assert parse_search_tokens("\t\n  \t") == []

    def test_strips_outer_whitespace_then_splits(self):
        assert parse_search_tokens("  ab  ") == ["ab"]

    def test_multiple_tokens_split_by_whitespace(self):
        assert parse_search_tokens("foo bar") == ["foo", "bar"]

    def test_collapses_internal_whitespace_runs(self):
        # str.split() with no args treats any whitespace run as one separator
        # and discards the empty pieces — important for "a    b" → ["a", "b"].
        assert parse_search_tokens("a    b\tc\nd") == ["a", "b", "c", "d"]

    def test_caps_at_max_search_tokens(self):
        # _MAX_SEARCH_TOKENS = 10; anything beyond must be silently dropped.
        q = " ".join(f"t{i}" for i in range(15))
        result = parse_search_tokens(q)
        assert len(result) == 10
        assert result == [f"t{i}" for i in range(10)]

    def test_exactly_at_cap_returns_all(self):
        q = " ".join(f"t{i}" for i in range(10))
        assert parse_search_tokens(q) == [f"t{i}" for i in range(10)]


# ------------------------------------------------------------------
# parse_search_query (free text + Gmail-style operators)
# ------------------------------------------------------------------
#
# The parser is a pure string operation: q in, ``ParsedSearchQuery`` out
# (no DB, no provider). It splits q into three independent buckets —
# free-text ``tokens`` (same semantics as ``parse_search_tokens``), typed
# ``operator_clauses`` ((kind, value) pairs the repository resolves), and a
# ``box_override`` from ``in:``. Tolerance policy: an unknown operator key
# stays literal free text; a known operator with an unsupported value is
# dropped. It never raises on the content of q.

class TestParseSearchQueryEmptyInputs:

    def test_none_returns_empty_parse(self):
        result = parse_search_query(None)
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_empty_string_returns_empty_parse(self):
        result = parse_search_query("")
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_only_whitespace_returns_empty_parse(self):
        result = parse_search_query("   \t\n  ")
        assert result.tokens == []
        assert result.operator_clauses == []
        assert result.box_override is None

    def test_plain_free_text_keeps_token_semantics(self):
        # No operator → identical free-text tokens to parse_search_tokens.
        assert parse_search_query("foo bar").tokens == ["foo", "bar"]
        assert parse_search_query("foo bar").operator_clauses == []


class TestParseSearchQueryTokenizationAndQuotes:

    def test_operator_with_quoted_value_separates_free_text(self):
        result = parse_search_query('from:"john doe" oferta')
        assert result.tokens == ["oferta"]
        assert result.operator_clauses == [("from_contains", "john doe")]

    def test_quoted_subject_value_is_a_single_clause(self):
        result = parse_search_query('subject:"acción requerida"')
        assert result.operator_clauses == [("subject_contains_op", "acción requerida")]
        assert result.tokens == []

    def test_quoted_free_text_phrase_is_one_token(self):
        result = parse_search_query('"frase libre"')
        assert result.tokens == ["frase libre"]
        assert result.operator_clauses == []

    def test_unterminated_quote_takes_rest_of_string(self):
        # An unclosed quote consumes everything after it as the value.
        result = parse_search_query('subject:"sin cerrar y mas')
        assert result.operator_clauses == [("subject_contains_op", "sin cerrar y mas")]


class TestParseSearchQueryOperators:

    def test_from_operator(self):
        assert parse_search_query("from:linkedin").operator_clauses == [
            ("from_contains", "linkedin"),
        ]

    def test_to_operator(self):
        assert parse_search_query("to:ana").operator_clauses == [
            ("to_contains", "ana"),
        ]

    def test_subject_operator(self):
        assert parse_search_query("subject:factura").operator_clauses == [
            ("subject_contains_op", "factura"),
        ]

    def test_has_attachment_singular(self):
        assert parse_search_query("has:attachment").operator_clauses == [
            ("has_attachments", True),
        ]

    def test_has_attachment_plural_alias(self):
        assert parse_search_query("has:attachments").operator_clauses == [
            ("has_attachments", True),
        ]

    def test_is_read(self):
        assert parse_search_query("is:read").operator_clauses == [
            ("is_read_op", True),
        ]

    def test_is_unread(self):
        assert parse_search_query("is:unread").operator_clauses == [
            ("is_read_op", False),
        ]

    def test_is_favorite(self):
        assert parse_search_query("is:favorite").operator_clauses == [
            ("is_favorite_op", True),
        ]

    def test_is_starred_is_alias_of_favorite(self):
        assert parse_search_query("is:starred").operator_clauses == [
            ("is_favorite_op", True),
        ]


class TestParseSearchQueryInOverride:

    def test_in_inbox_maps_to_all_mail(self):
        assert parse_search_query("in:inbox").box_override == "ALL_MAIL"

    def test_in_allmail_maps_to_all_mail(self):
        assert parse_search_query("in:allmail").box_override == "ALL_MAIL"

    def test_in_sent_maps_to_sent(self):
        assert parse_search_query("in:sent").box_override == "SENT"

    def test_in_spam_maps_to_spam(self):
        assert parse_search_query("in:spam").box_override == "SPAM"

    def test_in_trash_maps_to_trash(self):
        assert parse_search_query("in:trash").box_override == "TRASH"

    def test_in_is_not_an_operator_clause(self):
        # ``in:`` lives in ``box_override`` ONLY — it must never leak into
        # operator_clauses (the service applies it as a box override).
        result = parse_search_query("in:sent")
        assert result.operator_clauses == []

    def test_invalid_in_value_yields_no_override(self):
        assert parse_search_query("in:archivados").box_override is None

    def test_in_deleted_is_not_selectable(self):
        # ``DELETED`` is an internal "trash emptied" state the lupa must not
        # be able to target; ``in:deleted`` is an unsupported value.
        assert parse_search_query("in:deleted").box_override is None

    def test_last_valid_in_wins(self):
        assert parse_search_query("in:inbox in:trash").box_override == "TRASH"

    def test_later_invalid_in_does_not_clear_earlier_valid(self):
        assert parse_search_query("in:sent in:archivados").box_override == "SENT"


class TestParseSearchQueryDates:

    def test_before_slash_format_is_madrid_midnight(self):
        result = parse_search_query("before:2026/01/01")
        assert len(result.operator_clauses) == 1
        kind, value = result.operator_clauses[0]
        assert kind == "received_before"
        assert (value.year, value.month, value.day) == (2026, 1, 1)
        assert value.tzinfo is not None
        # Europe/Madrid, not a fixed UTC offset.
        assert value.tzinfo.key == "Europe/Madrid"

    def test_after_dash_format_is_madrid_midnight(self):
        result = parse_search_query("after:2026-01-01")
        kind, value = result.operator_clauses[0]
        assert kind == "received_after"
        assert (value.year, value.month, value.day) == (2026, 1, 1)
        assert value.tzinfo.key == "Europe/Madrid"

    def test_before_and_after_inclusivity_kinds(self):
        # after → received_after (>=, inclusive); before → received_before
        # (<, exclusive). The kind names encode the boundary the repository
        # builders translate to SQL.
        result = parse_search_query("after:2026-01-01 before:2026-02-01")
        kinds = {k for k, _ in result.operator_clauses}
        assert kinds == {"received_after", "received_before"}

    def test_invalid_month_is_dropped(self):
        assert parse_search_query("before:2026/13/01").operator_clauses == []

    def test_invalid_day_is_dropped(self):
        assert parse_search_query("before:2026/02/31").operator_clauses == []

    def test_non_zero_padded_date_is_dropped(self):
        # The regex requires fixed widths: 2026/1/1 does not match.
        assert parse_search_query("before:2026/1/1").operator_clauses == []

    def test_relative_date_word_is_dropped(self):
        assert parse_search_query("before:ayer").operator_clauses == []


class TestParseSearchQueryTolerance:

    def test_unknown_operator_becomes_literal_token(self):
        # ``foo`` is not a known operator, so the whole term (colon included)
        # is free text.
        result = parse_search_query("foo:bar")
        assert result.tokens == ["foo:bar"]
        assert result.operator_clauses == []

    def test_unsupported_is_value_is_dropped(self):
        result = parse_search_query("is:importante")
        assert result.operator_clauses == []
        assert result.tokens == []

    def test_unsupported_has_value_is_dropped(self):
        result = parse_search_query("has:drive")
        assert result.operator_clauses == []

    def test_empty_operator_value_is_dropped(self):
        # ``from:`` with no value (followed by whitespace) emits nothing.
        result = parse_search_query("from: oferta")
        assert result.operator_clauses == []
        assert result.tokens == ["oferta"]

    def test_never_raises_on_arbitrary_content(self):
        # Punctuation, accents, colons in the middle — none of it raises.
        parse_search_query('::: ¿qué? from:"a:b" 漢字 has:')


class TestParseSearchQueryCaseInsensitivity:

    def test_uppercase_operator_key_is_recognised(self):
        assert parse_search_query("FROM:linkedin").operator_clauses == [
            ("from_contains", "linkedin"),
        ]

    def test_mixed_case_is_key_and_value(self):
        assert parse_search_query("Is:Unread").operator_clauses == [
            ("is_read_op", False),
        ]

    def test_uppercase_in_value_is_recognised(self):
        assert parse_search_query("in:SENT").box_override == "SENT"

    def test_from_value_case_is_preserved(self):
        # Operator KEYS are case-insensitive, but the VALUE keeps its case
        # (the repository lowercases it for the ILIKE comparison).
        assert parse_search_query("from:LinkedIn").operator_clauses == [
            ("from_contains", "LinkedIn"),
        ]


class TestParseSearchQueryCombinationAndCaps:

    def test_mixed_free_text_and_operators_split_correctly(self):
        result = parse_search_query("hola from:linkedin mundo subject:oferta")
        assert result.tokens == ["hola", "mundo"]
        assert result.operator_clauses == [
            ("from_contains", "linkedin"),
            ("subject_contains_op", "oferta"),
        ]

    def test_free_text_tokens_capped_at_ten(self):
        q = " ".join(f"t{i}" for i in range(15))
        result = parse_search_query(q)
        assert len(result.tokens) == 10
        assert result.tokens == [f"t{i}" for i in range(10)]

    def test_operator_clauses_capped_at_ten(self):
        q = " ".join(f"from:s{i}" for i in range(15))
        result = parse_search_query(q)
        assert len(result.operator_clauses) == 10

    def test_repeated_operator_keeps_each_occurrence(self):
        # ``from:a from:b`` is two independent clauses (ANDed downstream),
        # not a single overwritten one.
        result = parse_search_query("from:a from:b")
        assert result.operator_clauses == [
            ("from_contains", "a"),
            ("from_contains", "b"),
        ]
