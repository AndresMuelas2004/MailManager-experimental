"""Tests espejo de ``core.email.helpers.reintentos`` (backoff D-16 y detalle de errores HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.email.helpers import http_error_detail, retry_with_backoff


# ── http_error_detail ───────────────────────────────────────────────


class TestHttpErrorDetail:
    def test_extracts_status_and_reason(self):
        exc = MagicMock()
        exc.resp.status = "404"
        exc.reason = "Not Found"
        status, reason = http_error_detail(exc)
        assert status == "404"
        assert reason == "Not Found"

    def test_missing_attrs_returns_unknown(self):
        exc = object()
        status, reason = http_error_detail(exc)
        assert status == "unknown"
        assert reason == "unknown"


# ── retry_with_backoff ─────────────────────────────────────────────


class TestRetryWithBackoff:

    def test_returns_value_on_first_success(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        result = retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 1

    def test_retries_retryable_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("network noise")
            return "ok"

        result = retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 3

    def test_propagates_non_retryable(self):
        def fn():
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)

    def test_raises_last_exc_after_exhausted_attempts(self):
        def fn():
            raise OSError("always fails")

        with pytest.raises(OSError):
            retry_with_backoff(fn, attempts=2, sleep=lambda _s: None)

    def test_zero_attempts_raises_value_error(self):
        with pytest.raises(ValueError):
            retry_with_backoff(lambda: "ok", attempts=0, sleep=lambda _s: None)

    def test_retry_after_extractor_overrides_default_delay(self):
        sleeps: list[float] = []

        def fn():
            raise OSError("retry")

        with pytest.raises(OSError):
            retry_with_backoff(
                fn,
                attempts=3,
                delays=(1.0, 2.0, 4.0),
                retry_after_extractor=lambda _exc: 7.5,
                sleep=lambda s: sleeps.append(s),
            )
        # All recorded sleeps must reflect the override, not the defaults.
        assert all(s == 7.5 for s in sleeps), sleeps
        assert len(sleeps) == 2  # attempts - 1 sleeps before final raise

    def test_custom_is_retryable_predicate(self):
        def fn():
            raise RuntimeError("custom")

        with pytest.raises(RuntimeError):
            retry_with_backoff(
                fn,
                attempts=2,
                is_retryable=lambda exc: isinstance(exc, RuntimeError),
                sleep=lambda _s: None,
            )
