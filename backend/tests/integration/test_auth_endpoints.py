"""
Integration tests for authentication endpoints and ownership enforcement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from auth import AuthSettingsError, AuthTokenInvalidError, AuthTokenNetworkError, AuthTokenProviderError

from api.routers.routers_helpers import require_session
from api.services import auth_service
from database import QueryError, session_store
from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL, TEST_USER_ID, TEST_USER_EMAIL


# ------------------------------------------------------------------
# POST /auth/google — happy path
# ------------------------------------------------------------------

def test_google_login_success(test_client_base, isolated_db, monkeypatch, app):
    """Monkeypatch Google verification, verify cookie set and user returned."""
    fake_id_info = {
        "sub": "google-sub-login-test",
        "email": "login@example.com",
        "name": "Login User",
        "picture": "https://example.com/avatar.png",
        "email_verified": True,
    }
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "valid-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Login successful."
    assert data["user"]["email"] == "login@example.com"
    assert "session_id" in resp.cookies


def test_google_login_invalid_token(test_client_base, isolated_db, monkeypatch, app):
    """Invalid token raises Unauthorized -> 401."""
    def _raise(*_a, **_kw):
        raise AuthTokenInvalidError("Invalid token")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "bad-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_google_login_auth_settings_error(test_client_base, isolated_db, monkeypatch, app):
    """AuthSettingsError from get_auth_settings -> 500 env_var_error."""
    def _raise():
        raise AuthSettingsError("missing GOOGLE_CLIENT_ID")

    monkeypatch.setattr(auth_service, "get_auth_settings", _raise)

    resp = test_client_base.post("/auth/google", json={"id_token": "any-token"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "env_var_error"


def test_google_login_network_error(test_client_base, isolated_db, monkeypatch, app):
    """AuthTokenNetworkError from verify_google_token -> 502 external_api_error."""
    def _raise(*_a, **_kw):
        raise AuthTokenNetworkError("connection refused")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "any-token"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "external_api_error"


def test_google_login_provider_error(test_client_base, isolated_db, monkeypatch, app):
    """AuthTokenProviderError from verify_google_token -> 401 unauthorized."""
    def _raise(*_a, **_kw):
        raise AuthTokenProviderError("provider rejected")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "any-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


# ------------------------------------------------------------------
# POST /auth/google — missing claims
# ------------------------------------------------------------------

def test_google_login_missing_sub_claim(test_client_base, isolated_db, monkeypatch, app):
    """Token without 'sub' claim -> 401."""
    fake_id_info = {"email": "x@y.com", "name": "No Sub"}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "valid-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_google_login_missing_email_claim(test_client_base, isolated_db, monkeypatch, app):
    """Token without 'email' claim -> 401."""
    fake_id_info = {"sub": "sub-1", "name": "No Email"}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "valid-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_google_login_unverified_email(test_client_base, isolated_db, monkeypatch, app):
    """Token with email_verified=False -> 401 (H2 verified-email guard, end-to-end)."""
    fake_id_info = {
        "sub": "sub-unverified",
        "email": "unverified@example.com",
        "name": "Unverified User",
        "email_verified": False,
    }
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp = test_client_base.post("/auth/google", json={"id_token": "valid-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


# ------------------------------------------------------------------
# POST /auth/microsoft
# ------------------------------------------------------------------

def _set_ms_env(monkeypatch):
    """Both env vars are required: _load_auth_settings still demands GOOGLE_CLIENT_ID."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "test-client-id")


def test_microsoft_login_success(test_client_base, isolated_db, monkeypatch, app):
    """Monkeypatch Microsoft verification, verify cookie set and user returned."""
    claims = {
        "sub": "entra-sub-login-test",
        "email": "ms-login@contoso.com",
        "name": "MS Login User",
        "tid": "11111111-2222-4333-8444-555555555555",
    }
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: claims,
    )
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Login successful."
    assert data["user"]["email"] == "ms-login@contoso.com"
    assert "session_id" in resp.cookies


def test_microsoft_login_invalid_token(test_client_base, isolated_db, monkeypatch, app):
    """AuthTokenInvalidError -> 401 unauthorized."""
    def _raise(*_a, **_kw):
        raise AuthTokenInvalidError("Invalid token")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _raise)
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "bad-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_microsoft_login_network_error(test_client_base, isolated_db, monkeypatch, app):
    """AuthTokenNetworkError -> 502 external_api_error (not 401)."""
    def _raise(*_a, **_kw):
        raise AuthTokenNetworkError("JWKS unreachable")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _raise)
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "any-token"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "external_api_error"


def test_microsoft_login_falls_back_to_preferred_username(test_client_base, isolated_db, monkeypatch, app):
    """email absent, preferred_username present -> 200 with email = preferred_username."""
    claims = {
        "sub": "entra-sub-upn",
        "preferred_username": "upn@contoso.com",
        "name": "UPN User",
        "tid": "11111111-2222-4333-8444-555555555555",
    }
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: claims,
    )
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "upn@contoso.com"


def test_microsoft_login_missing_email_and_preferred_username(test_client_base, isolated_db, monkeypatch, app):
    """Neither email nor preferred_username -> 401."""
    claims = {"sub": "entra-sub-noemail", "name": "No Email"}
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: claims,
    )
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_microsoft_login_missing_sub_claim(test_client_base, isolated_db, monkeypatch, app):
    """Token without 'sub' claim -> 401 (mirrors the Google missing-sub guard)."""
    claims = {
        "email": "ms-nosub@contoso.com",
        "name": "No Sub",
        "tid": "11111111-2222-4333-8444-555555555555",
    }
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: claims,
    )
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_microsoft_login_does_not_require_email_verified(test_client_base, isolated_db, monkeypatch, app):
    """email_verified=False must NOT block Microsoft login — deliberate asymmetry
    with Google (Entra does not emit email_verified). A future 'harmonisation' that
    added the Google guard here would silently break org-account logins; this test
    locks the contract end-to-end."""
    claims = {
        "sub": "entra-sub-unverified",
        "email": "ms-unverified@contoso.com",
        "name": "MS Unverified",
        "email_verified": False,
        "tid": "11111111-2222-4333-8444-555555555555",
    }
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: claims,
    )
    _set_ms_env(monkeypatch)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "ms-unverified@contoso.com"


def test_microsoft_login_not_configured(test_client_base, isolated_db, monkeypatch, app):
    """MICROSOFT_CLIENT_ID absent (GOOGLE_CLIENT_ID set) -> 500 env_var_error."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)

    def _must_not_run(*_a, **_kw):
        raise AssertionError("verify_microsoft_token must not run when unconfigured")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _must_not_run)

    resp = test_client_base.post("/auth/microsoft", json={"id_token": "valid-token"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "env_var_error"


# ------------------------------------------------------------------
# Identity model (migration 0037): composite (auth_provider, provider_sub)
# ------------------------------------------------------------------

def test_login_idempotent_same_provider_sub_returns_same_user(test_client_base, isolated_db, monkeypatch, app):
    """Logging in twice with the same (auth_provider, provider_sub) returns the SAME
    user_id and creates exactly one users row (migration 0037 ON CONFLICT upsert)."""
    fake_id_info = {
        "sub": "google-sub-idempotent",
        "email": "idem@example.com",
        "name": "Idem User",
        "email_verified": True,
    }
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    resp1 = test_client_base.post("/auth/google", json={"id_token": "tok"})
    resp2 = test_client_base.post("/auth/google", json={"id_token": "tok"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["user"]["user_id"] == resp2.json()["user"]["user_id"]

    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE auth_provider = %s AND provider_sub = %s",
            ("google", "google-sub-idempotent"),
        )
        assert cur.fetchone()[0] == 1


def test_same_email_two_providers_creates_distinct_users(test_client_base, isolated_db, monkeypatch, app):
    """No account-linking (migration 0037): the same email logging in via Google and
    via Microsoft produces TWO distinct user rows / user_ids — the composite identity
    key, not the email, is what disambiguates."""
    shared_email = "shared@example.com"
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: {
            "sub": "g-sub-shared", "email": shared_email,
            "name": "Shared G", "email_verified": True,
        },
    )
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {
            "sub": "ms-sub-shared", "email": shared_email,
            "name": "Shared M", "tid": "11111111-2222-4333-8444-555555555555",
        },
    )
    _set_ms_env(monkeypatch)

    g_resp = test_client_base.post("/auth/google", json={"id_token": "tok"})
    ms_resp = test_client_base.post("/auth/microsoft", json={"id_token": "tok"})
    assert g_resp.status_code == 200
    assert ms_resp.status_code == 200
    assert g_resp.json()["user"]["user_id"] != ms_resp.json()["user"]["user_id"]

    with isolated_db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE email = %s", (shared_email,))
        assert cur.fetchone()[0] == 2


# ------------------------------------------------------------------
# GET /auth/me
# ------------------------------------------------------------------

def test_get_me_authenticated(test_client):
    """Authenticated user can fetch their profile with exact field values."""
    resp = test_client.get("/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == TEST_USER_ID
    assert data["email"] == TEST_USER_EMAIL
    assert data["name"] == "Test User"


def test_get_me_no_session(test_client_base, isolated_db, app):
    """No cookie -> 401."""
    # Temporarily remove the override to test real behavior.
    override = app.dependency_overrides.pop(require_session, None)
    try:
        resp = test_client_base.get("/auth/me")
        assert resp.status_code == 401
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override


def test_get_me_expired_session(test_client_base, isolated_db, app):
    """Expired session -> 401."""
    override = app.dependency_overrides.pop(require_session, None)
    try:
        # Create an already-expired session.
        session_id = str(uuid4())
        with isolated_db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (session_id, user_id, expires_at)
                VALUES (%(session_id)s, %(user_id)s, %(expires_at)s)
                """,
                {
                    "session_id": session_id,
                    "user_id": TEST_USER_ID,
                    "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                },
            )
        resp = test_client_base.get("/auth/me", cookies={"session_id": session_id})
        assert resp.status_code == 401
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override


def test_get_me_user_deleted_returns_not_found(test_client, isolated_db):
    """GET /auth/me after the user row is deleted → 404 user_not_found."""
    with isolated_db.cursor() as cur:
        cur.execute("DELETE FROM users WHERE user_id = %s", (TEST_USER_ID,))
    resp = test_client.get("/auth/me")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"


# ------------------------------------------------------------------
# POST /auth/logout
# ------------------------------------------------------------------

def test_logout(test_client_base, isolated_db, monkeypatch, app):
    """Login then logout clears session."""
    fake_id_info = {
        "sub": "google-sub-logout-test",
        "email": "logout@example.com",
        "name": "Logout User",
        "email_verified": True,
    }
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    login_resp = test_client_base.post("/auth/google", json={"id_token": "tok"})
    assert login_resp.status_code == 200

    session_cookie = login_resp.cookies.get("session_id")
    logout_resp = test_client_base.post("/auth/logout", cookies={"session_id": session_cookie})
    assert logout_resp.status_code == 200
    assert logout_resp.json() == {"status": "logged_out"}


def test_logout_database_error(test_client_base, isolated_db, monkeypatch, app):
    """DB error during session deletion -> 503 database_query_error."""
    fake_id_info = {
        "sub": "google-sub-logout-db-error",
        "email": "logoutdberr@example.com",
        "name": "Logout DB Error",
        "email_verified": True,
    }
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    login_resp = test_client_base.post("/auth/google", json={"id_token": "tok"})
    assert login_resp.status_code == 200
    session_cookie = login_resp.cookies.get("session_id")

    def _raise(session_id):
        raise QueryError("DB fail")

    monkeypatch.setattr(session_store, "delete", _raise)
    logout_resp = test_client_base.post("/auth/logout", cookies={"session_id": session_cookie})
    assert logout_resp.status_code == 503
    assert logout_resp.json()["error"]["code"] == "database_query_error"


def test_logout_without_session_cookie(test_client, monkeypatch):
    """POST /auth/logout without session cookie → 200 (idempotent)."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    resp = test_client.post("/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"status": "logged_out"}


# ------------------------------------------------------------------
# Ownership
# ------------------------------------------------------------------

def test_create_mailbox_sets_owner(test_client):
    """Created mailbox has owner_user_id set to the authenticated user."""
    resp = test_client.post(_MAILBOX_URL, json={"display_name": "Owned MB"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner_user_id"] == TEST_USER_ID


def test_list_mailboxes_filtered_by_owner(test_client, isolated_db):
    """Each user only sees their own mailboxes."""
    # Create a mailbox for the test user (via the overridden session).
    test_client.post(_MAILBOX_URL, json={"display_name": "My MB"})

    # Create a second user and a mailbox owned by them directly in the DB.
    other_user_id = str(uuid4())
    other_mailbox_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, auth_provider, provider_sub, email)
            VALUES (%(user_id)s, %(auth_provider)s, %(provider_sub)s, %(email)s)
            """,
            {
                "user_id": other_user_id,
                "auth_provider": "google",
                "provider_sub": "other-sub",
                "email": "other@example.com",
            },
        )
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mailbox_id)s, %(display_name)s, %(owner_user_id)s)
            """,
            {
                "mailbox_id": other_mailbox_id,
                "display_name": "Other MB",
                "owner_user_id": other_user_id,
            },
        )

    resp = test_client.get(_MAILBOX_URL)
    assert resp.status_code == 200
    ids = [m["mailbox_id"] for m in resp.json()]
    assert other_mailbox_id not in ids


def test_protected_endpoint_no_auth(test_client_base, isolated_db, app):
    """GET /mailboxes without session cookie -> 401."""
    override = app.dependency_overrides.pop(require_session, None)
    try:
        resp = test_client_base.get(_MAILBOX_URL)
        assert resp.status_code == 401
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override


# ------------------------------------------------------------------
# DELETE /auth/me
# ------------------------------------------------------------------

def test_delete_account_success(test_client, isolated_db, monkeypatch):
    """Deleting own account removes user, cascades to mailboxes, clears cookie."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

    # Create a mailbox so we can verify cascade.
    mb_resp = test_client.post(_MAILBOX_URL, json={"display_name": "To be cascaded"})
    assert mb_resp.status_code == 200

    resp = test_client.delete("/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"status": "account_deleted"}

    # Cookie should be cleared.
    assert resp.headers.get("set-cookie") is not None
    assert "session_id" in resp.headers["set-cookie"]

    # User row should be gone.
    with isolated_db.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id = %s", (TEST_USER_ID,))
        assert cur.fetchone() is None

    # Mailboxes should be gone (CASCADE).
    with isolated_db.cursor() as cur:
        cur.execute("SELECT 1 FROM mailboxes WHERE owner_user_id = %s", (TEST_USER_ID,))
        assert cur.fetchone() is None


def test_delete_account_user_gone(test_client, isolated_db):
    """Deleting a user that was already removed returns 404."""
    # Remove the test user directly.
    with isolated_db.cursor() as cur:
        cur.execute("DELETE FROM users WHERE user_id = %s", (TEST_USER_ID,))

    resp = test_client.delete("/auth/me")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"


def test_delete_account_no_session(test_client_base, isolated_db, app):
    """DELETE /auth/me without session cookie -> 401."""
    override = app.dependency_overrides.pop(require_session, None)
    try:
        resp = test_client_base.delete("/auth/me")
        assert resp.status_code == 401
    finally:
        if override is not None:
            app.dependency_overrides[require_session] = override


def test_mailbox_ownership_forbidden(test_client, isolated_db):
    """User A's mailbox accessed by user B -> 403."""
    other_user_id = str(uuid4())
    other_mailbox_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, auth_provider, provider_sub, email)
            VALUES (%(user_id)s, %(auth_provider)s, %(provider_sub)s, %(email)s)
            """,
            {
                "user_id": other_user_id,
                "auth_provider": "google",
                "provider_sub": "forbidden-sub",
                "email": "b@example.com",
            },
        )
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mailbox_id)s, %(display_name)s, %(owner_user_id)s)
            """,
            {
                "mailbox_id": other_mailbox_id,
                "display_name": "Forbidden MB",
                "owner_user_id": other_user_id,
            },
        )

    resp = test_client.get(f"{_MAILBOX_URL}/{other_mailbox_id}")
    assert resp.status_code == 403


# ------------------------------------------------------------------
# NULL owner_user_id defense-in-depth
# ------------------------------------------------------------------

def test_null_owner_mailbox_forbidden(test_client, isolated_db):
    """A mailbox with NULL owner_user_id must not be accessible."""
    orphan_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute("ALTER TABLE mailboxes ALTER COLUMN owner_user_id DROP NOT NULL")
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mailbox_id)s, %(display_name)s, NULL)
            """,
            {"mailbox_id": orphan_id, "display_name": "Orphan MB"},
        )

    resp = test_client.get(f"{_MAILBOX_URL}/{orphan_id}")
    assert resp.status_code == 403


def test_null_owner_mailbox_not_listed(test_client, isolated_db):
    """A mailbox with NULL owner_user_id must not appear in the listing."""
    orphan_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute("ALTER TABLE mailboxes ALTER COLUMN owner_user_id DROP NOT NULL")
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mailbox_id)s, %(display_name)s, NULL)
            """,
            {"mailbox_id": orphan_id, "display_name": "Orphan MB"},
        )

    resp = test_client.get(_MAILBOX_URL)
    assert resp.status_code == 200
    ids = [m["mailbox_id"] for m in resp.json()]
    assert orphan_id not in ids


# ------------------------------------------------------------------
# 3E: 403 on endpoints accessing another user's mailbox
# ------------------------------------------------------------------

def _create_foreign_mailbox(isolated_db) -> str:
    """Create a mailbox owned by another user and return its ID."""
    other_user_id = str(uuid4())
    other_mailbox_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, auth_provider, provider_sub, email)
            VALUES (%(user_id)s, %(auth_provider)s, %(provider_sub)s, %(email)s)
            """,
            {
                "user_id": other_user_id,
                "auth_provider": "google",
                "provider_sub": f"sub-{other_user_id[:8]}",
                "email": "other@e.com",
            },
        )
        cur.execute(
            """
            INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
            VALUES (%(mailbox_id)s, %(display_name)s, %(owner_user_id)s)
            """,
            {"mailbox_id": other_mailbox_id, "display_name": "Foreign", "owner_user_id": other_user_id},
        )
    return other_mailbox_id


def test_create_account_on_foreign_mailbox_forbidden(test_client, isolated_db):
    mid = _create_foreign_mailbox(isolated_db)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_send_email_on_foreign_mailbox_forbidden(test_client, isolated_db):
    mid = _create_foreign_mailbox(isolated_db)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/emails/send",
        json={"account_id": "x", "subject": "S", "body": "B", "recipients": ["a@b.com"]},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_delete_foreign_mailbox_forbidden(test_client, isolated_db):
    mid = _create_foreign_mailbox(isolated_db)
    resp = test_client.delete(f"{_MAILBOX_URL}/{mid}")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_create_draft_on_foreign_mailbox_forbidden(test_client, isolated_db):
    mid = _create_foreign_mailbox(isolated_db)
    resp = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts/00000000-0000-4000-a000-000000000099/drafts",
        json={
            "to_recipients": ["a@b.com"],
            "subject": "x",
            "body": "x",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
