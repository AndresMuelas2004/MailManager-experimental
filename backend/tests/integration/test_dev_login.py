"""
Integration tests for the dev-login backdoor endpoint:

``POST /auth/dev-login``

Four documented states:

- ``DEV_LOGIN_ENABLED`` unset/false → 503 ``dev_login_disabled``.
- ``client.host`` outside ``DEV_LOGIN_TRUSTED_HOSTS`` → 403
  ``dev_login_not_localhost``.
- ``DEV_LOGIN_EMAIL`` unset → 500 ``env_var_error`` (config bug, not a
  security guard).
- ``DEV_LOGIN_EMAIL`` set to an email not present in ``users`` → 404
  ``user_not_found``.

Plus the happy path: the cookie returned by ``/auth/dev-login`` must
let a subsequent ``GET /auth/me`` resolve the same user. The happy path
removes the ``require_session`` override so the real cookie is the only
thing keeping the test green — mirrors ``test_get_me_no_session``.
"""

from __future__ import annotations

import pytest

from api.routers.routers_helpers import require_session
from tests.integration.conftest import TEST_USER_EMAIL, TEST_USER_ID


_DEV_LOGIN_URL = "/auth/dev-login"
_ENABLED_ENV = "DEV_LOGIN_ENABLED"
_EMAIL_ENV = "DEV_LOGIN_EMAIL"
_TRUSTED_HOSTS_ENV = "DEV_LOGIN_TRUSTED_HOSTS"


# ── env unset → 503 dev_login_disabled ────────────────────────────


def test_dev_login_returns_503_when_env_unset(test_client_base, monkeypatch):
    monkeypatch.delenv(_ENABLED_ENV, raising=False)
    response = test_client_base.post(_DEV_LOGIN_URL)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dev_login_disabled"


def test_dev_login_returns_503_when_env_is_false(test_client_base, monkeypatch):
    monkeypatch.setenv(_ENABLED_ENV, "false")
    response = test_client_base.post(_DEV_LOGIN_URL)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dev_login_disabled"


# ── host not in trusted set → 403 dev_login_not_localhost ──────────


def test_dev_login_returns_403_when_client_host_not_trusted(test_client_base, monkeypatch):
    """TestClient reports ``testclient`` as the host; restrict the
    allowlist so the request is refused."""
    monkeypatch.setenv(_ENABLED_ENV, "true")
    monkeypatch.setenv(_TRUSTED_HOSTS_ENV, "10.0.0.1")  # excludes "testclient"
    response = test_client_base.post(_DEV_LOGIN_URL)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "dev_login_not_localhost"


# ── email env unset → 500 env_var_error ────────────────────────────


def test_dev_login_returns_500_when_email_env_unset(test_client_base, monkeypatch):
    monkeypatch.setenv(_ENABLED_ENV, "true")
    monkeypatch.setenv(_TRUSTED_HOSTS_ENV, "testclient")
    monkeypatch.delenv(_EMAIL_ENV, raising=False)
    response = test_client_base.post(_DEV_LOGIN_URL)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "env_var_error"


# ── email not in DB → 404 user_not_found ───────────────────────────


def test_dev_login_returns_404_when_user_not_in_db(test_client_base, monkeypatch):
    monkeypatch.setenv(_ENABLED_ENV, "true")
    monkeypatch.setenv(_TRUSTED_HOSTS_ENV, "testclient")
    monkeypatch.setenv(_EMAIL_ENV, "ghost@example.com")  # not seeded
    response = test_client_base.post(_DEV_LOGIN_URL)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


# ── happy path: cookie set + GET /auth/me resolves the same user ───


def test_dev_login_happy_path_sets_cookie_and_resolves_user(
    test_client_base, isolated_db, monkeypatch, app,
):
    """End-to-end check of the cookie contract.

    The follow-up ``GET /auth/me`` lives in the same test (per
    common_mistakes.md §1: integration follow-up assertions belong with
    the operation under test, not in a separate case)."""
    monkeypatch.setenv(_ENABLED_ENV, "true")
    monkeypatch.setenv(_TRUSTED_HOSTS_ENV, "testclient")
    monkeypatch.setenv(_EMAIL_ENV, TEST_USER_EMAIL)  # seeded in conftest._seed_test_user
    # GOOGLE_CLIENT_ID is read by _load_auth_settings even though dev_login
    # does not use Google verification — share the precedent of test_auth_endpoints.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    # Remove the require_session override so the real cookie is what
    # carries authentication through the follow-up GET /auth/me.
    override = app.dependency_overrides.pop(require_session, None)
    try:
        response = test_client_base.post(_DEV_LOGIN_URL)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["user"]["user_id"] == TEST_USER_ID
        assert data["user"]["email"] == TEST_USER_EMAIL
        assert data["message"] == "Dev login successful."

        session_cookie = response.cookies.get("session_id")
        assert session_cookie is not None

        me_response = test_client_base.get(
            "/auth/me",
            cookies={"session_id": session_cookie},
        )
        assert me_response.status_code == 200
        assert me_response.json()["user_id"] == TEST_USER_ID
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override
