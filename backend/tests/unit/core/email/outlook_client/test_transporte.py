"""Tests espejo de ``outlook_client.transporte`` (_graph_request y parseo de fechas Graph)."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.email.errors import EmailExternalAPIError
from core.email.outlook_client import _parse_graph_datetime

from ._helpers import _make_authenticated_client


# ── _graph_request ───────────────────────────────────────────────


class TestGraphRequest:
    def _mock_response(self, body_bytes: bytes, status: int = 200):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body_bytes
        mock_resp.status = status
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_happy_path(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b'{"value": []}')
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = client._graph_request("GET", "https://graph.microsoft.com/v1.0/me")
        assert result == {"value": []}
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer token"

    def test_204_returns_empty_dict(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b"", status=204)
        with patch("urllib.request.urlopen", return_value=resp):
            result = client._graph_request("POST", "https://graph.microsoft.com/v1.0/me/sendMail")
        assert result == {}

    def test_http_error_raises_external_api(self):
        client = _make_authenticated_client()
        exc = urllib.error.HTTPError(
            "https://graph.microsoft.com", 403, "Forbidden",
            {}, MagicMock(read=lambda: b'{"error": {"code": "Forbidden", "message": "no access"}}'),
        )
        exc.read = lambda: b'{"error": {"code": "Forbidden", "message": "no access"}}'
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(EmailExternalAPIError, match="Graph API call"):
                client._graph_request("GET", "https://graph.microsoft.com/v1.0/me")

    def test_post_sends_json_body(self):
        client = _make_authenticated_client()
        resp = self._mock_response(b'{}')
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            client._graph_request("POST", "https://graph.microsoft.com/v1.0/me/sendMail", body={"key": "val"})
        req = mock_open.call_args[0][0]
        assert req.get_header("Content-type") == "application/json"
        assert json.loads(req.data) == {"key": "val"}


# ── _parse_graph_datetime ───────────────────────────────────────────


class TestParseGraphDatetime:
    """Module-level helper shared by _parse_graph_message and fetch_conversation."""

    def test_parses_iso_z_suffix(self):
        result = _parse_graph_datetime("2025-06-01T12:00:00Z")
        assert result.year == 2025
        assert result.month == 6
        assert result.tzinfo is not None

    def test_empty_falls_back_to_now(self):
        result = _parse_graph_datetime("")
        assert (datetime.now(timezone.utc) - result).total_seconds() < 5

    def test_none_falls_back_to_now(self):
        result = _parse_graph_datetime(None)
        assert (datetime.now(timezone.utc) - result).total_seconds() < 5

    def test_malformed_falls_back_to_now(self):
        result = _parse_graph_datetime("not-a-date")
        assert (datetime.now(timezone.utc) - result).total_seconds() < 5
