"""Tests espejo de ``outlook_client.cliente`` (etiqueta de cuenta y guard clauses sin autenticar)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.email.errors import (
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailNotAuthenticatedError,
    EmailRecipientsMissingError,
)
from core.email.outlook_client import OutlookClient


# ── get_account_label ────────────────────────────────────────────────


def test_get_account_label_returns_constructor_value(client: OutlookClient):
    assert client.get_account_label() == "mb__outlook"


# ── Guard clauses ────────────────────────────────────────────────────


class TestGuardClauses:
    def test_begin_interactive_auth_missing_credentials_raises_error(self, client: OutlookClient):
        with pytest.raises(EmailMissingAppCredentialsError):
            client.begin_interactive_auth(app_credentials=None)

    def test_begin_interactive_auth_missing_credentials_empty_dict_raises_error(self, client: OutlookClient):
        with pytest.raises(EmailMissingAppCredentialsError):
            client.begin_interactive_auth(app_credentials={})

    def test_authenticate_silent_missing_credentials_raises_error(self, client: OutlookClient):
        with pytest.raises(EmailMissingAppCredentialsError):
            client.authenticate_silent(app_credentials=None)

    def test_authenticate_silent_missing_access_token_raises_error(self, client: OutlookClient):
        creds = {"client_id": "id", "client_secret": "s", "tenant": "t"}
        with pytest.raises(EmailMissingTokenError):
            client.authenticate_silent(app_credentials=creds, user_tokens={})

    def test_authenticate_silent_expired_no_refresh_token_raises_error(
        self, client: OutlookClient
    ):
        creds = {"client_id": "id", "client_secret": "secret", "tenant": "t"}
        tokens = {
            "access_token": "at",
            "refresh_token": None,
            "expiry": "2020-01-01T00:00:00",
        }
        with pytest.raises(EmailMissingRefreshTokenError):
            client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

    def test_authenticate_silent_not_expired_sets_access_token_returns_none(
        self, client: OutlookClient
    ):
        creds = {"client_id": "id", "client_secret": "secret", "tenant": "t"}
        future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tokens = {
            "access_token": "valid_token",
            "refresh_token": "rt",
            "expiry": future_expiry,
        }
        result = client.authenticate_silent(app_credentials=creds, user_tokens=tokens)
        assert result is None
        assert client._access_token == "valid_token"

    def test_authenticate_silent_missing_client_id_or_secret_raises_error(
        self, client: OutlookClient
    ):
        creds = {"tenant": "t"}
        tokens = {
            "access_token": "at",
            "refresh_token": "rt",
            "expiry": "2020-01-01T00:00:00",
        }
        with pytest.raises(EmailMissingAppCredentialsError, match="Missing required"):
            client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

    def test_fetch_email_metadata_not_authenticated_raises_error(self, client: OutlookClient):
        """Unauthenticated fetch raises EmailNotAuthenticatedError."""
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_email_metadata()

    def test_send_email_not_authenticated_raises_error(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.send_email("subj", "body", ["a@b.com"])

    def test_send_email_empty_recipients_raises_error(self, client: OutlookClient):
        client._access_token = "token"
        with pytest.raises(EmailRecipientsMissingError):
            client.send_email("subj", "body", [])
