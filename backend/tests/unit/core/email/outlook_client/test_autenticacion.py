"""Tests espejo de ``outlook_client.autenticacion`` (PKCE, refresco, token request y perfil)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from core.email.errors import (
    EmailExternalAPIError,
    EmailMissingAppCredentialsError,
    EmailRefreshFailedError,
)
from core.email.outlook_client import OUTLOOK_SCOPES, OutlookClient

from ._helpers import _make_authenticated_client


# ── _token_url ───────────────────────────────────────────────────────


def test_token_url_returns_correct_format():
    url = OutlookClient._token_url("my-tenant")
    assert url == "https://login.microsoftonline.com/my-tenant/oauth2/v2.0/token"


# ── _compute_expiry ──────────────────────────────────────────────────


class TestComputeExpiry:
    def test_valid_seconds(self):
        before = datetime.now(timezone.utc)
        result = OutlookClient._compute_expiry(3600)
        after = datetime.now(timezone.utc) + timedelta(seconds=3600)
        parsed = datetime.fromisoformat(result)
        assert before <= parsed <= after

    def test_none_returns_none(self):
        assert OutlookClient._compute_expiry(None) is None

    def test_negative_clamped_to_zero(self):
        before = datetime.now(timezone.utc)
        result = OutlookClient._compute_expiry(-10)
        parsed = datetime.fromisoformat(result)
        assert before <= parsed <= before + timedelta(seconds=2)

    def test_invalid_type_returns_none(self):
        assert OutlookClient._compute_expiry("not-a-number") is None


# ── _resolve_scopes ──────────────────────────────────────────────────


class TestResolveScopes:
    def test_from_credentials_list(self, client: OutlookClient):
        creds = {"scopes": ["scope1", "scope2"]}
        result = client._resolve_scopes(creds)
        assert result == ["scope1", "scope2"]

    def test_from_credentials_string_comma_separated(self, client: OutlookClient):
        creds = {"scopes": "scope1,scope2,scope3"}
        result = client._resolve_scopes(creds)
        assert result == ["scope1", "scope2", "scope3"]

    def test_from_credentials_string_space_separated(self, client: OutlookClient):
        creds = {"scopes": "scope1 scope2 scope3"}
        result = client._resolve_scopes(creds)
        assert result == ["scope1", "scope2", "scope3"]

    def test_from_token_payload_fallback(self, client: OutlookClient):
        creds = {}
        token_payload = {"scopes": ["tok_scope1"]}
        result = client._resolve_scopes(creds, token_payload)
        assert result == ["tok_scope1"]

    def test_defaults_to_outlook_scopes(self, client: OutlookClient):
        result = client._resolve_scopes({})
        assert result == list(OUTLOOK_SCOPES)


# ── authenticate_silent refresh path (mock _token_request) ───────────


class TestAuthenticateSilentRefreshPath:
    """Test the refresh logic by mocking _token_request."""

    def _make_expired_setup(self):
        creds = {
            "client_id": "cid",
            "client_secret": "csecret",
            "tenant": "my-tenant",
        }
        tokens = {
            "access_token": "old_at",
            "refresh_token": "old_rt",
            "expiry": "2020-01-01T00:00:00",
        }
        return creds, tokens

    def test_expired_refreshes_and_returns_wrapped_tokens(self, client: OutlookClient):
        creds, tokens = self._make_expired_setup()
        mock_response = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_in": 3600,
        }
        with patch.object(client, "_token_request", return_value=mock_response):
            result = client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

        assert result is not None
        assert isinstance(result["access_token"], SecretStr)
        assert result["access_token"].get_secret_value() == "new_at"
        assert isinstance(result["refresh_token"], SecretStr)
        assert result["refresh_token"].get_secret_value() == "new_rt"
        assert client._access_token == "new_at"

    def test_refresh_failure_raises_refresh_failed(self, client: OutlookClient):
        creds, tokens = self._make_expired_setup()
        with patch.object(
            client, "_token_request", side_effect=Exception("network error")
        ):
            with pytest.raises(EmailRefreshFailedError, match="network error"):
                client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

    def test_refresh_missing_access_token_raises_refresh_failed(self, client: OutlookClient):
        creds, tokens = self._make_expired_setup()
        mock_response = {"refresh_token": "new_rt"}
        with patch.object(client, "_token_request", return_value=mock_response):
            with pytest.raises(EmailRefreshFailedError, match="missing access_token"):
                client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

    def test_refresh_preserves_rotated_refresh_token(self, client: OutlookClient):
        creds, tokens = self._make_expired_setup()
        mock_response = {
            "access_token": "new_at",
            "refresh_token": "rotated_rt",
            "expires_in": 3600,
        }
        with patch.object(client, "_token_request", return_value=mock_response):
            result = client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

        assert result["refresh_token"].get_secret_value() == "rotated_rt"

    def test_refresh_no_new_refresh_token_keeps_original(self, client: OutlookClient):
        """When Microsoft doesn't rotate the refresh token, the original is preserved."""
        creds, tokens = self._make_expired_setup()
        mock_response = {
            "access_token": "new_at",
            "expires_in": 3600,
        }
        with patch.object(client, "_token_request", return_value=mock_response):
            result = client.authenticate_silent(app_credentials=creds, user_tokens=tokens)

        assert result["refresh_token"].get_secret_value() == "old_rt"


class TestFetchSenderProfile:
    def test_caches_result(self):
        """_fetch_sender_profile calls /me only once, caches for subsequent calls."""
        client = _make_authenticated_client()
        profile_response = {
            "displayName": "Test User",
            "mail": "me@outlook.com",
        }
        with patch.object(client, "_graph_request", return_value=profile_response) as mock_graph:
            first = client._fetch_sender_profile()
            second = client._fetch_sender_profile()
        assert first == ("me@outlook.com", "Test User")
        assert second == ("me@outlook.com", "Test User")
        mock_graph.assert_called_once()

    def test_falls_back_to_jwt_when_graph_fails(self):
        """When /me fails (e.g. missing User.Read scope), falls back to JWT claims."""
        import base64 as b64
        claims = json.dumps({
            "preferred_username": "user@outlook.com",
            "name": "JWT User",
        }).encode()
        payload_b64 = b64.urlsafe_b64encode(claims).rstrip(b"=").decode()
        fake_token = f"header.{payload_b64}.signature"

        client = OutlookClient(account_label="mb__outlook")
        client._access_token = fake_token
        with patch.object(client, "_graph_request", side_effect=RuntimeError("403")):
            result = client._fetch_sender_profile()
        assert result == ("user@outlook.com", "JWT User")

    def test_falls_back_to_sentitems_when_jwt_also_fails(self):
        """When /me and JWT fail, reads sender from sentitems."""
        client = OutlookClient(account_label="mb__outlook")
        client._access_token = "EwB-opaque-token"  # not a JWT

        sentitems_response = {
            "value": [{
                "from": {"emailAddress": {"address": "me@outlook.com", "name": "Sent User"}},
            }],
        }

        def mock_graph(method, url, body=None):
            if "mailFolders" not in url and "/me?" in url:
                raise RuntimeError("no User.Read scope")
            if "sentitems/messages" in url:
                return sentitems_response
            raise AssertionError(f"Unexpected: {method} {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client._fetch_sender_profile()
        assert result == ("me@outlook.com", "Sent User")

    def test_returns_empty_when_all_paths_fail(self):
        """When /me, JWT, and sentitems all fail, returns empty strings."""
        client = OutlookClient(account_label="mb__outlook")
        client._access_token = "opaque"
        with patch.object(client, "_graph_request", side_effect=RuntimeError("boom")):
            result = client._fetch_sender_profile()
        assert result == ("", "")


# ── _token_request ───────────────────────────────────────────────


class TestTokenRequest:
    def _mock_response(self, body_bytes: bytes, status: int = 200):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body_bytes
        mock_resp.status = status
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_happy_path(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b'{"access_token": "tok123"}')
        with patch("urllib.request.urlopen", return_value=resp):
            result = client._token_request("https://login.example.com/token", {"grant_type": "authorization_code"})
        assert result["access_token"] == "tok123"

    def test_http_error_raises_external_api(self):
        client = _make_authenticated_client()
        exc = urllib.error.HTTPError(
            "https://login.example.com/token", 400, "Bad Request",
            {}, MagicMock(read=lambda: b'{"error": "invalid_grant", "error_description": "bad"}'),
        )
        exc.read = lambda: b'{"error": "invalid_grant", "error_description": "bad"}'
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(EmailExternalAPIError, match="token endpoint"):
                client._token_request("https://login.example.com/token", {})

    def test_url_error_raises_external_api(self):
        client = _make_authenticated_client()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS fail")):
            with pytest.raises(EmailExternalAPIError, match="reach token endpoint"):
                client._token_request("https://login.example.com/token", {})

    def test_malformed_json_raises_external_api(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b"not-json")
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(EmailExternalAPIError, match="invalid JSON"):
                client._token_request("https://login.example.com/token", {})

    def test_error_in_response_raises_external_api(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b'{"error": "invalid_grant", "error_description": "token expired"}')
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(EmailExternalAPIError, match="invalid_grant"):
                client._token_request("https://login.example.com/token", {})


# ── authenticate (interactive OAuth + PKCE) ─────────────────────


_VALID_OUTLOOK_APP_CREDS = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "tenant": "test-tenant",
    "redirect_uri": "http://localhost:8400/callback",
    "scopes": OUTLOOK_SCOPES,
}


class TestInteractiveAuth:

    def _make_creds(self, **overrides):
        creds = dict(_VALID_OUTLOOK_APP_CREDS)
        creds.update(overrides)
        return creds

    def test_begin_returns_authorization_url_with_pkce_and_state(self):
        client = OutlookClient(account_label="mb__outlook")
        creds = self._make_creds()

        with patch(
            "core.email.outlook_client.autenticacion.secrets.token_urlsafe",
            side_effect=["x" * 128, "state-value"],
        ):
            result = client.begin_interactive_auth(app_credentials=creds)

        parsed = urllib.parse.urlparse(result["authorization_url"])
        query = urllib.parse.parse_qs(parsed.query)
        assert parsed.netloc == "login.microsoftonline.com"
        assert query["redirect_uri"] == [creds["redirect_uri"]]
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] == ["state-value"]
        assert result["state"] == "state-value"
        assert result["flow_state"]["code_verifier"] == "x" * 128
        assert result["flow_state"]["redirect_uri"] == creds["redirect_uri"]
        assert result["flow_state"]["tenant"] == creds["tenant"]

    def test_begin_redirect_override_wins_over_credentials(self):
        client = OutlookClient(account_label="mb__outlook")
        creds = self._make_creds()
        override = "http://localhost:8000/auth/outlook/callback"

        result = client.begin_interactive_auth(app_credentials=creds, redirect_uri=override)

        query = urllib.parse.parse_qs(urllib.parse.urlparse(result["authorization_url"]).query)
        assert query["redirect_uri"] == [override]
        assert result["flow_state"]["redirect_uri"] == override

    def test_begin_missing_app_credentials_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailMissingAppCredentialsError):
            client.begin_interactive_auth(app_credentials=None)

    def test_begin_missing_redirect_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        creds = self._make_creds(redirect_uri="")
        with pytest.raises(EmailMissingAppCredentialsError):
            client.begin_interactive_auth(app_credentials=creds)

    def test_complete_exchanges_code_with_pkce_verifier(self):
        client = OutlookClient(account_label="mb__outlook")
        creds = self._make_creds()
        captured: dict = {}

        def _fake_token_request(url, payload):
            captured["url"] = url
            captured["payload"] = payload
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

        with (
            patch.object(client, "_token_request", side_effect=_fake_token_request),
            patch.object(client, "_fetch_sender_profile", return_value=("user@outlook.com", "User")),
        ):
            result = client.complete_interactive_auth(
                app_credentials=creds,
                flow_state={
                    "code_verifier": "v" * 43,
                    "redirect_uri": creds["redirect_uri"],
                    "tenant": creds["tenant"],
                    "scopes": list(OUTLOOK_SCOPES),
                },
                code="auth-code-123",
            )

        assert captured["payload"]["grant_type"] == "authorization_code"
        assert captured["payload"]["code"] == "auth-code-123"
        assert captured["payload"]["code_verifier"] == "v" * 43
        assert captured["payload"]["redirect_uri"] == creds["redirect_uri"]
        assert result["access_token"].get_secret_value() == "at"
        assert result["email_address"] == "user@outlook.com"
        assert client._access_token == "at"

    def test_complete_missing_code_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailExternalAPIError, match="authorization code"):
            client.complete_interactive_auth(
                app_credentials=self._make_creds(),
                flow_state={"code_verifier": "v" * 43, "redirect_uri": "http://localhost:8000/cb"},
                code="",
            )

    def test_complete_missing_flow_state_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with pytest.raises(EmailExternalAPIError, match="flow state"):
            client.complete_interactive_auth(
                app_credentials=self._make_creds(), flow_state={}, code="auth-code-123",
            )

    def test_complete_missing_access_token_in_response_raises(self):
        client = OutlookClient(account_label="mb__outlook")
        with patch.object(client, "_token_request", return_value={}):
            with pytest.raises(EmailExternalAPIError, match="missing access_token"):
                client.complete_interactive_auth(
                    app_credentials=self._make_creds(),
                    flow_state={
                        "code_verifier": "v" * 43,
                        "redirect_uri": "http://localhost:8000/cb",
                    },
                    code="auth-code-123",
                )
