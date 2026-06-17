"""
Unit tests for the rate-limit ``Depends`` factories in
``api.routers.routers_helpers`` (``rate_limit_by_ip`` / ``rate_limit_by_user``).

The factories return a synchronous dependency that inspects a ``Request`` and
consults the pure engine. The tests call the returned callable directly with a
stub ``Request`` (same style as the ``enforce_multipart_size_limit`` tests,
plus a ``.client`` with ``.host`` for the IP path). Calling the dependency
outside FastAPI means ``user_id: str = Depends(require_session)`` is just a
default value (a ``Depends`` object), so the user identity is injected by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import rate_limit
from api.errors.exceptions import TooManyRequests
from api.routers.routers_helpers import rate_limit_by_ip, rate_limit_by_user


def _request(headers: dict[str, str] | None = None, host: str | None = "1.2.3.4"):
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(headers=headers or {}, client=client)


@pytest.fixture(autouse=True)
def _isolate_counters():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMITS", {"global": ((2, 60),)})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")


class TestDisabledByDefault:

    def test_rate_limit_by_ip_is_noop_when_flag_absent(self, monkeypatch, small_limits):
        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        dep = rate_limit_by_ip("global")
        # Well past the limit, but the flag is off → never raises.
        for _ in range(10):
            dep(_request())

    def test_rate_limit_by_user_is_noop_when_flag_absent(self, monkeypatch, small_limits):
        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        dep = rate_limit_by_user("global")
        for _ in range(10):
            dep(_request(), user_id="u1")


class TestByIpEnabled:

    def test_raises_too_many_requests_with_scope_and_retry_after(
        self, enabled, small_limits
    ):
        dep = rate_limit_by_ip("global")
        dep(_request())
        dep(_request())
        with pytest.raises(TooManyRequests) as excinfo:
            dep(_request())
        exc = excinfo.value
        assert exc.code == "rate_limit_exceeded"
        assert exc.detail["scope"] == "global"
        assert exc.detail["retry_after"] > 0

    def test_uses_first_forwarded_for_hop_as_identity(self, enabled, small_limits):
        dep = rate_limit_by_ip("global")
        # Two requests from 9.9.9.9 exhaust its window...
        dep(_request(headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}))
        dep(_request(headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}))
        with pytest.raises(TooManyRequests):
            dep(_request(headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}))
        # ...while a different first hop is tracked independently.
        dep(_request(headers={"x-forwarded-for": "8.8.8.8"}))

    def test_falls_back_to_client_host_without_header(self, enabled, small_limits):
        dep = rate_limit_by_ip("global")
        dep(_request(host="5.5.5.5"))
        dep(_request(host="5.5.5.5"))
        with pytest.raises(TooManyRequests):
            dep(_request(host="5.5.5.5"))
        # A different peer host has its own counter.
        dep(_request(host="6.6.6.6"))


class TestByUserEnabled:

    def test_raises_too_many_requests_for_the_user(self, enabled, small_limits):
        dep = rate_limit_by_user("global")
        dep(_request(), user_id="u1")
        dep(_request(), user_id="u1")
        with pytest.raises(TooManyRequests) as excinfo:
            dep(_request(), user_id="u1")
        assert excinfo.value.detail["scope"] == "global"
        assert excinfo.value.detail["retry_after"] > 0

    def test_distinct_users_have_independent_counters(self, enabled, small_limits):
        dep = rate_limit_by_user("global")
        dep(_request(), user_id="u1")
        dep(_request(), user_id="u1")
        with pytest.raises(TooManyRequests):
            dep(_request(), user_id="u1")
        # A different user is unaffected by u1's exhausted window.
        dep(_request(), user_id="u2")
