"""Tests espejo de ``gmail_client._comunes`` (reintentos y parseo de direcciones)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from core.email.gmail_client import _is_retryable, _split_address_header
from core.email.gmail_client._comunes import _gmail_error_reasons


# ── _is_retryable ───────────────────────────────────────────────────


class TestIsRetryable:
    def _make_http_error(self, status: int, content: bytes = b"err") -> Exception:
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        type(resp).status = status
        return HttpError(resp=resp, content=content)

    def _make_403_with_reason(self, reason: str) -> Exception:
        body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode("utf-8")
        return self._make_http_error(403, content=body)

    def test_404_not_retryable(self):
        assert _is_retryable(self._make_http_error(404)) is False

    def test_400_not_retryable(self):
        assert _is_retryable(self._make_http_error(400)) is False

    def test_403_without_rate_limit_reason_not_retryable(self):
        # A 403 whose body carries no recognised rate-limit reason (here an
        # unparseable body) stays permanent — Gmail throttles only under the
        # two rate-limit reasons.
        assert _is_retryable(self._make_http_error(403)) is False

    def test_403_rate_limit_exceeded_is_retryable(self):
        # Gmail surfaces per-project throttling as HTTP 403 rateLimitExceeded
        # (NOT 429), so the fix makes it retryable.
        assert _is_retryable(self._make_403_with_reason("rateLimitExceeded")) is True

    def test_403_user_rate_limit_exceeded_is_retryable(self):
        assert _is_retryable(self._make_403_with_reason("userRateLimitExceeded")) is True

    def test_403_daily_limit_exceeded_not_retryable(self):
        # Exhausted daily quota is a hard wall — retrying only burns it.
        assert _is_retryable(self._make_403_with_reason("dailyLimitExceeded")) is False

    def test_403_unknown_reason_not_retryable(self):
        assert _is_retryable(self._make_403_with_reason("insufficientPermissions")) is False

    def test_410_not_retryable(self):
        assert _is_retryable(self._make_http_error(410)) is False

    def test_401_not_retryable(self):
        assert _is_retryable(self._make_http_error(401)) is False

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


class TestGmailErrorReasons:
    """The reason-parsing helper used by the 403 retry classification."""

    def _http_error(self, content) -> Exception:
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        type(resp).status = 403
        return HttpError(resp=resp, content=content)

    def test_extracts_reasons_from_valid_body(self):
        body = json.dumps(
            {"error": {"errors": [{"reason": "rateLimitExceeded"}, {"reason": "backendError"}]}}
        ).encode("utf-8")
        assert _gmail_error_reasons(self._http_error(body)) == {"rateLimitExceeded", "backendError"}

    def test_empty_content_returns_empty_set(self):
        assert _gmail_error_reasons(self._http_error(b"")) == set()

    def test_malformed_json_returns_empty_set(self):
        assert _gmail_error_reasons(self._http_error(b"{not json")) == set()

    def test_non_dict_payload_returns_empty_set(self):
        assert _gmail_error_reasons(self._http_error(b"[1, 2, 3]")) == set()

    def test_missing_error_key_returns_empty_set(self):
        assert _gmail_error_reasons(self._http_error(b'{"foo": "bar"}')) == set()

    def test_errors_entries_without_reason_are_skipped(self):
        body = json.dumps({"error": {"errors": [{"message": "no reason here"}]}}).encode("utf-8")
        assert _gmail_error_reasons(self._http_error(body)) == set()


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
