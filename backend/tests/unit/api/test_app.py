"""
Unit tests for the application factory ``create_app`` — CORS startup guard (H1)
and the rate-limiting error-hierarchy contract (#11e).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import pytest

from api.app import create_app
from api.errors.exceptions import ApiError, TooManyRequests
from api.errors.handlers import _STATUS_MAP, register_error_handlers


def test_create_app_rejects_wildcard_cors_origin(monkeypatch):
    """A wildcard origin is incompatible with credentialed CORS → fail to boot."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        create_app()


def test_create_app_rejects_wildcard_among_explicit_origins(monkeypatch):
    """A wildcard mixed in with explicit origins is still refused."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com,*")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        create_app()


def test_create_app_accepts_explicit_origins(monkeypatch):
    """Explicit origins build the app without error."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "https://app.example.com,https://admin.example.com"
    )
    app = create_app()
    assert app is not None


def test_create_app_builds_with_rate_limiting_enabled(monkeypatch):
    """Adding the per-router rate-limit ``dependencies`` does not break boot.

    The flag is consulted per request inside the dependencies, not in the
    factory, so this only confirms the wiring mounts cleanly when it is on.
    """
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
    app = create_app()
    assert app is not None


def test_too_many_requests_error_code():
    """The 429 error carries the stable ``rate_limit_exceeded`` code."""
    assert TooManyRequests.code == "rate_limit_exceeded"


def test_too_many_requests_maps_to_429():
    """The status map routes the rate-limit error to HTTP 429."""
    assert _STATUS_MAP[TooManyRequests] == status.HTTP_429_TOO_MANY_REQUESTS


# --- Retry-After header emission by the generic ApiError handler (#11e) -------


def _client_for_error(exc: ApiError) -> TestClient:
    """Minimal app whose only route raises ``exc`` through the real handlers."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def _boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_handler_emits_retry_after_header_from_detail():
    """A throttling error's ``detail.retry_after`` becomes the ``Retry-After`` header.

    The header is the half of the contract that is NOT CORS-exposed, so a unit
    test must lock it independently of the integration suite.
    """
    client = _client_for_error(
        TooManyRequests("throttled", {"scope": "global", "retry_after": 12})
    )
    resp = client.get("/boom")
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resp.headers["Retry-After"] == "12"
    # The value also rides in the JSON body (the cross-origin SPA reads it there).
    assert resp.json()["error"]["detail"]["retry_after"] == 12


def test_handler_omits_retry_after_header_for_non_retry_error():
    """An ApiError without ``retry_after`` in its detail produces no header."""
    client = _client_for_error(ApiError("plain failure"))
    resp = client.get("/boom")
    assert "Retry-After" not in resp.headers


# --- 5xx cause-chain logging by the typed ApiError handler --------------------


def _api_error_with_cause() -> ApiError:
    """Build an ApiError chained ``from`` a driver-level root cause."""
    try:
        try:
            raise ValueError("driver-level root cause")
        except ValueError as root:
            raise ApiError("wrapped operation failed") from root
    except ApiError as exc:
        return exc


def test_handler_logs_5xx_with_cause_chain(caplog):
    """A server-side ApiError logs at ERROR with the full ``from exc`` chain.

    The layers below wrap-and-rethrow without logging, so the handler is the
    single point where the original driver/provider exception becomes
    observable in the logs.
    """
    client = _client_for_error(_api_error_with_cause())
    with caplog.at_level(logging.ERROR, logger="api.errors.handlers"):
        resp = client.get("/boom")
    assert resp.status_code == 500
    records = [r for r in caplog.records if r.name == "api.errors.handlers"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    # The rendered traceback carries the chained root cause.
    assert "driver-level root cause" in caplog.text


def test_handler_does_not_log_4xx(caplog):
    """Expected client errors (4xx) stay unlogged — they are not server faults."""
    client = _client_for_error(TooManyRequests("throttled"))
    with caplog.at_level(logging.DEBUG, logger="api.errors.handlers"):
        resp = client.get("/boom")
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert [r for r in caplog.records if r.name == "api.errors.handlers"] == []


# --- Global per-IP rate-limit wiring: exemptions (#11e) -----------------------


def _has_ip_rate_limit_dep(route: APIRoute) -> bool:
    """True if the route carries a ``rate_limit_by_ip`` dependency (the only
    IP-keyed buckets are ``auth_login`` on /auth and the ``global`` safety net)."""
    return any(
        getattr(dep.call, "__qualname__", "").startswith("rate_limit_by_ip")
        for dep in route.dependant.dependencies
    )


def test_health_and_oauth_callbacks_are_exempt_from_global_rate_limit(monkeypatch):
    """``/health`` and the OAuth callback routes must carry no per-IP dependency.

    The provider redirects the user's browser to the callbacks, so a 429 there
    would break a legitimate account connection — the global net is deliberately
    omitted. This static guard catches a regression that adds it back.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
    app = create_app()
    exempt = {"/health", "/auth/google/callback", "/auth/outlook/callback"}
    found = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in exempt:
            found.add(route.path)
            assert not _has_ip_rate_limit_dep(route), (
                f"{route.path} must stay exempt from the global per-IP rate limit"
            )
    assert found == exempt  # every exempt route was actually present in the app


def test_non_auth_routers_carry_the_global_per_ip_rate_limit(monkeypatch):
    """A non-auth, non-exempt route proves the ``global`` per-IP net is wired.

    Such a route can only obtain a ``rate_limit_by_ip`` dependency from the
    router-level ``global`` include (``auth_login`` lives only on /auth), so its
    presence confirms the safety net is mounted across the protected surface.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
    app = create_app()
    exempt = {"/health", "/auth/google/callback", "/auth/outlook/callback"}
    covered = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path not in exempt
        and not route.path.startswith("/auth")
        and _has_ip_rate_limit_dep(route)
    ]
    assert covered, "expected non-auth routers to carry the global per-IP rate limit"
