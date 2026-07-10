"""Tests espejo de ``services_helpers.contexto_cuentas``: acceso a mailbox, manager, errores silenciosos de auth, credenciales y tokens."""

from __future__ import annotations

import logging

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from api.errors.exceptions import (
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
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    unwrap_secret,
)
from core.email.errors import (
    EmailAuthError,
    EmailExternalAPIError,
    EmailMissingTokenError,
)
from database import CredentialReadError, QueryError


class TestEnsureMailboxAccess:

    def test_null_owner_raises_forbidden(self):
        """A mailbox with owner_user_id=None must be rejected."""
        fake_record = {
            "mailbox_id": "mb-1",
            "display_name": "Orphan",
            "owner_user_id": None,
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
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
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
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
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
            mock_store.get.return_value = fake_record
            result = ensure_mailbox_access("mb-1", "owner-a")
        assert result == fake_record

    def test_database_error_translated(self):
        """QueryError from mailbox_store.get → DatabaseQueryError."""
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
            mock_store.get.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                ensure_mailbox_access("mb-1", "some-user-id")

    def test_generic_exception_raises_api_error(self):
        """RuntimeError from mailbox_store.get → ApiError fallback."""
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
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
            "api.services.services_helpers.contexto_cuentas.EmailManager"
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
        with patch("api.services.services_helpers.contexto_cuentas.mailbox_store") as mock_store:
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
# load_wrapped_app_credentials
# ------------------------------------------------------------------

class TestLoadWrappedAppCredentials:

    def test_happy_path_wraps_client_secret(self):
        fake_creds = {"client_id": "cid", "client_secret": "secret"}
        with patch("api.services.services_helpers.contexto_cuentas.load_app_credentials", return_value=fake_creds):
            result = load_wrapped_app_credentials("gmail")
        assert isinstance(result["client_secret"], SecretStr)
        assert result["client_secret"].get_secret_value() == "secret"
        assert result["client_id"] == "cid"

    def test_database_error_translated(self):
        with patch(
            "api.services.services_helpers.contexto_cuentas.load_app_credentials",
            side_effect=CredentialReadError("read fail"),
        ):
            with pytest.raises(CredentialFileError):
                load_wrapped_app_credentials("gmail")

    def test_generic_exception_raises_api_error(self):
        with patch(
            "api.services.services_helpers.contexto_cuentas.load_app_credentials",
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
        with patch("api.services.services_helpers.contexto_cuentas.account_store") as mock_store:
            mock_store.get_tokens.return_value = fake_tokens
            result = load_wrapped_account_tokens("mb-1", "acc-1", "gmail")
        assert isinstance(result["access_token"], SecretStr)
        assert isinstance(result["refresh_token"], SecretStr)
        assert result["expires_in"] == 3600

    def test_none_returns_empty_dict(self):
        with patch("api.services.services_helpers.contexto_cuentas.account_store") as mock_store:
            mock_store.get_tokens.return_value = None
            result = load_wrapped_account_tokens("mb-1", "acc-1", "gmail")
        assert result == {}

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.contexto_cuentas.account_store") as mock_store:
            mock_store.get_tokens.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                load_wrapped_account_tokens("mb-1", "acc-1", "gmail")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.contexto_cuentas.account_store") as mock_store:
            mock_store.get_tokens.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to load account tokens"):
                load_wrapped_account_tokens("mb-1", "acc-1", "gmail")


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
