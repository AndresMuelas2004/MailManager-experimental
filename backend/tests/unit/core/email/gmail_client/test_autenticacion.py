"""Tests espejo de ``gmail_client.autenticacion`` (config OAuth y flujo interactivo)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.email.errors import (
    EmailExternalAPIError,
    EmailInvalidCredentialsDataError,
    EmailProviderConfigError,
)
from core.email.gmail_client import GmailClient


# ── _build_client_config ─────────────────────────────────────────────


class TestBuildClientConfig:
    def test_wraps_flat_dict_in_installed(self, client: GmailClient):
        payload = {"client_id": "id", "client_secret": "secret"}
        result = client._build_client_config(payload)
        assert result == {"installed": payload}

    def test_preserves_installed_key(self, client: GmailClient):
        payload = {"installed": {"client_id": "id"}}
        result = client._build_client_config(payload)
        assert result is payload

    def test_preserves_web_key(self, client: GmailClient):
        payload = {"web": {"client_id": "id"}}
        result = client._build_client_config(payload)
        assert result is payload


# ── interactive auth (begin / complete) ──────────────────────────


class TestInteractiveAuth:
    _REDIRECT = "http://localhost:8000/auth/google/callback"
    _CREDS = {"client_id": "id", "client_secret": "secret"}

    @patch("core.email.gmail_client.autenticacion.InstalledAppFlow")
    def test_begin_returns_authorization_url_state_and_flow(self, mock_flow_cls, client: GmailClient):
        """Happy path: the flow is built with our redirect and the URL/state/flow are returned."""
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?mock=1", "state-123",
        )
        mock_flow_cls.from_client_config.return_value = mock_flow

        result = client.begin_interactive_auth(app_credentials=self._CREDS, redirect_uri=self._REDIRECT)

        _, kwargs = mock_flow_cls.from_client_config.call_args
        assert kwargs["redirect_uri"] == self._REDIRECT
        mock_flow.authorization_url.assert_called_once_with(access_type="offline", prompt="consent")
        assert result["authorization_url"].startswith("https://accounts.google.com/")
        assert result["state"] == "state-123"
        assert result["flow_state"]["flow"] is mock_flow

    def test_begin_missing_redirect_raises_provider_config(self, client: GmailClient):
        with pytest.raises(EmailProviderConfigError, match="redirect_uri"):
            client.begin_interactive_auth(app_credentials=self._CREDS, redirect_uri="")

    @patch("core.email.gmail_client.autenticacion.InstalledAppFlow")
    def test_begin_flow_build_failure_raises_invalid_credentials_data(self, mock_flow_cls, client: GmailClient):
        mock_flow_cls.from_client_config.side_effect = ValueError("bad config")

        with pytest.raises(EmailInvalidCredentialsDataError, match="failed to build OAuth flow"):
            client.begin_interactive_auth(app_credentials=self._CREDS, redirect_uri=self._REDIRECT)

    @patch("core.email.gmail_client.autenticacion.InstalledAppFlow")
    def test_begin_authorization_url_failure_raises_external_api(self, mock_flow_cls, client: GmailClient):
        mock_flow = MagicMock()
        mock_flow.authorization_url.side_effect = RuntimeError("unexpected")
        mock_flow_cls.from_client_config.return_value = mock_flow

        with pytest.raises(EmailExternalAPIError, match="authorization URL"):
            client.begin_interactive_auth(app_credentials=self._CREDS, redirect_uri=self._REDIRECT)

    @patch("core.email.gmail_client.autenticacion.build")
    def test_complete_exchanges_code_and_returns_wrapped_tokens(self, mock_build, client: GmailClient):
        """Happy path: the code is exchanged on the begin-time flow and tokens are wrapped."""
        mock_creds = MagicMock()
        mock_creds.token = "access-tok"
        mock_creds.refresh_token = "refresh-tok"
        mock_creds.expiry = None
        mock_creds.scopes = ["https://mail.google.com/"]
        mock_flow = MagicMock()
        mock_flow.credentials = mock_creds

        with patch.object(client, "_fetch_sender_email", return_value="user@gmail.com"):
            result = client.complete_interactive_auth(
                flow_state={"flow": mock_flow}, code="auth-code-123",
            )

        mock_flow.fetch_token.assert_called_once_with(code="auth-code-123")
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)
        assert client.service is not None
        assert result["access_token"].get_secret_value() == "access-tok"
        assert result["refresh_token"].get_secret_value() == "refresh-tok"
        assert result["email_address"] == "user@gmail.com"

    def test_complete_missing_code_raises_external_api(self, client: GmailClient):
        with pytest.raises(EmailExternalAPIError, match="authorization code"):
            client.complete_interactive_auth(flow_state={"flow": MagicMock()}, code="  ")

    def test_complete_missing_flow_state_raises_external_api(self, client: GmailClient):
        with pytest.raises(EmailExternalAPIError, match="flow state"):
            client.complete_interactive_auth(flow_state={}, code="auth-code-123")

    def test_complete_exchange_failure_raises_external_api(self, client: GmailClient):
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = RuntimeError("exchange failed")

        with pytest.raises(EmailExternalAPIError, match="exchange the authorization code"):
            client.complete_interactive_auth(flow_state={"flow": mock_flow}, code="auth-code-123")
