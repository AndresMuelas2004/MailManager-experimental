"""Tests espejo de ``gmail_client._comunes`` (reintentos y parseo de direcciones)."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.email.gmail_client import _is_retryable, _split_address_header


# ── _is_retryable ───────────────────────────────────────────────────


class TestIsRetryable:
    def _make_http_error(self, status: int) -> Exception:
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        type(resp).status = status
        return HttpError(resp=resp, content=b"err")

    def test_404_not_retryable(self):
        assert _is_retryable(self._make_http_error(404)) is False

    def test_400_not_retryable(self):
        assert _is_retryable(self._make_http_error(400)) is False

    def test_403_not_retryable(self):
        assert _is_retryable(self._make_http_error(403)) is False

    def test_410_not_retryable(self):
        assert _is_retryable(self._make_http_error(410)) is False

    def test_429_retryable(self):
        assert _is_retryable(self._make_http_error(429)) is True

    def test_500_retryable(self):
        assert _is_retryable(self._make_http_error(500)) is True

    def test_502_retryable(self):
        assert _is_retryable(self._make_http_error(502)) is True

    def test_503_retryable(self):
        assert _is_retryable(self._make_http_error(503)) is True

    def test_504_retryable(self):
        assert _is_retryable(self._make_http_error(504)) is True

    def test_non_http_error_retryable(self):
        assert _is_retryable(RuntimeError("timeout")) is True

    def test_generic_exception_retryable(self):
        assert _is_retryable(Exception("network error")) is True


class TestSplitAddressHeader:
    """Covers the RFC 5322 address parsing helper used by fetch_reply_context."""

    def test_single_address_no_display_name(self):
        assert _split_address_header("ana@example.com") == ["ana@example.com"]

    def test_single_address_with_display_name(self):
        assert _split_address_header("Ana López <ana@example.com>") == ["ana@example.com"]

    def test_multiple_addresses_comma_separated(self):
        out = _split_address_header(
            'Ana <ana@x.com>, "Bob, Jr." <bob@y.com>, charlie@z.com',
        )
        assert "ana@x.com" in out
        assert "bob@y.com" in out
        assert "charlie@z.com" in out

    def test_empty_string_returns_empty_list(self):
        assert _split_address_header("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _split_address_header("   ") == []

    def test_none_value_returns_empty_list(self):
        assert _split_address_header(None) == []

    def test_address_without_at_dropped(self):
        # ``Undisclosed recipients:;`` and similar malformed senders
        # produce display-only entries — those are silently dropped.
        out = _split_address_header("Undisclosed recipients:;")
        assert out == []

    def test_address_with_angle_brackets_only(self):
        assert _split_address_header("<bare@x.com>") == ["bare@x.com"]
