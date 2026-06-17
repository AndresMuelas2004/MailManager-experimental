"""
Unit tests for the pure in-process rate-limit engine (``api.rate_limit``).

The engine is framework-agnostic and accepts an injected ``now``, so every
test is deterministic with no real sleeping. ``RATE_LIMITS`` is the single
source of truth for the figures; the engine reads it on every ``check`` call,
so the tests monkeypatch it to small, readable limits instead of coupling to
the production profile.
"""

from __future__ import annotations

import pytest

from api import rate_limit


@pytest.fixture(autouse=True)
def _isolate_counters():
    """Every test starts and ends with empty counters."""
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def small_limits(monkeypatch):
    """Replace the production profile with tiny, deterministic windows."""
    monkeypatch.setattr(
        rate_limit,
        "RATE_LIMITS",
        {
            "single": ((2, 60),),
            "double": ((2, 60), (3, 3600)),
            "other": ((2, 60),),
        },
    )


class TestCheckSingleWindow:

    def test_allows_requests_below_the_limit(self, small_limits):
        assert rate_limit.check("single", "ip1", now=0.0) is None
        assert rate_limit.check("single", "ip1", now=0.0) is None

    def test_blocks_once_the_limit_is_exceeded(self, small_limits):
        rate_limit.check("single", "ip1", now=0.0)
        rate_limit.check("single", "ip1", now=0.0)
        retry_after = rate_limit.check("single", "ip1", now=0.0)
        assert retry_after is not None
        # Deterministic at t=0 with a 60s window: the whole window remains, plus
        # the +1s ceiling guard → 61. Asserting the exact value (not just > 0)
        # locks the seconds_to_reset formula against off-by-one / sign drift.
        assert retry_after == 61

    def test_window_rollover_resets_the_counter(self, small_limits):
        # Exhaust the minute window at t=0.
        rate_limit.check("single", "ip1", now=0.0)
        rate_limit.check("single", "ip1", now=0.0)
        assert rate_limit.check("single", "ip1", now=0.0) is not None
        # Advancing into the next minute window starts a fresh count.
        assert rate_limit.check("single", "ip1", now=60.0) is None


class TestCheckMultipleWindows:

    def test_minute_window_blocks_before_hour_window(self, small_limits):
        # double = ((2, 60), (3, 3600)). The minute window (limit 2) trips on
        # the 3rd request even though the hour window (limit 3) is still under.
        assert rate_limit.check("double", "ip1", now=0.0) is None
        assert rate_limit.check("double", "ip1", now=0.0) is None
        retry_after = rate_limit.check("double", "ip1", now=0.0)
        assert retry_after is not None
        assert retry_after > 0

    def test_hour_window_keeps_counting_across_minute_rollovers(self, small_limits):
        # 3 requests in the first minute: the minute trips on the 3rd, but all
        # three increment the hour counter (it is incremented before the check).
        rate_limit.check("double", "ip1", now=0.0)
        rate_limit.check("double", "ip1", now=0.0)
        minute_retry = rate_limit.check("double", "ip1", now=0.0)
        assert minute_retry is not None
        # Next minute: the minute window reopens, but the hour window is now at
        # 4 > 3, so the request is still blocked — by the hour window this time.
        hour_retry = rate_limit.check("double", "ip1", now=60.0)
        assert hour_retry is not None
        # The most restrictive exceeded window wins: the hour's wait (rest of
        # the hour) is larger than the minute's wait would have been.
        assert hour_retry > minute_retry


class TestCheckIsolation:

    def test_distinct_identities_have_independent_counters(self, small_limits):
        rate_limit.check("single", "ip1", now=0.0)
        rate_limit.check("single", "ip1", now=0.0)
        assert rate_limit.check("single", "ip1", now=0.0) is not None
        # A different identity in the same bucket is unaffected.
        assert rate_limit.check("single", "ip2", now=0.0) is None

    def test_distinct_buckets_have_independent_counters(self, small_limits):
        rate_limit.check("single", "ip1", now=0.0)
        rate_limit.check("single", "ip1", now=0.0)
        assert rate_limit.check("single", "ip1", now=0.0) is not None
        # The same identity in a different bucket has its own counter.
        assert rate_limit.check("other", "ip1", now=0.0) is None


class TestCheckEdgeCases:

    def test_unknown_bucket_is_a_no_op(self, small_limits):
        # Defensive no-op: an unconfigured bucket never throttles.
        for _ in range(100):
            assert rate_limit.check("does-not-exist", "ip1", now=0.0) is None

    def test_reset_clears_counters(self, small_limits):
        rate_limit.check("single", "ip1", now=0.0)
        rate_limit.check("single", "ip1", now=0.0)
        assert rate_limit.check("single", "ip1", now=0.0) is not None
        rate_limit.reset()
        # After reset the same identity starts from zero again.
        assert rate_limit.check("single", "ip1", now=0.0) is None


class TestRateLimitingEnabled:

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", value)
        assert rate_limit.rate_limiting_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "anything"])
    def test_falsy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", value)
        assert rate_limit.rate_limiting_enabled() is False

    def test_unset_disables(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        assert rate_limit.rate_limiting_enabled() is False


class TestClientIp:

    def test_first_hop_of_forwarded_for_wins(self):
        ip = rate_limit.client_ip("9.9.9.9, 1.1.1.1, 2.2.2.2", "10.0.0.1")
        assert ip == "9.9.9.9"

    def test_forwarded_for_is_trimmed(self):
        ip = rate_limit.client_ip("  9.9.9.9 , 1.1.1.1", "10.0.0.1")
        assert ip == "9.9.9.9"

    def test_falls_back_to_client_host_without_header(self):
        assert rate_limit.client_ip(None, "10.0.0.1") == "10.0.0.1"

    def test_falls_back_to_unknown_without_anything(self):
        assert rate_limit.client_ip(None, None) == "unknown"

    def test_empty_forwarded_for_falls_back_to_client_host(self):
        assert rate_limit.client_ip("", "10.0.0.1") == "10.0.0.1"

    def test_whitespace_only_forwarded_for_falls_back_to_client_host(self):
        # A malformed "   " header must not become an empty-string identity that
        # would collapse every such client into a single shared bucket.
        assert rate_limit.client_ip("   ", "10.0.0.1") == "10.0.0.1"
