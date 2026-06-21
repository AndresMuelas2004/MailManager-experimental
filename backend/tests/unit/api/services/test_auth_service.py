"""
Unit tests for the auth service layer.

All database stores are monkeypatched to avoid real DB calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from auth import AuthError, AuthTokenInvalidError, AuthTokenNetworkError

from database import DatabaseError, QueryError

from api.errors.exceptions import (
    ApiError,
    DevLoginDisabled,
    DevLoginNotLocalhost,
    EnvVarError,
    ExternalAPIError,
    SessionOperationError,
    Unauthorized,
    UserNotFound,
    UserOperationError,
)
from api.schemas.auth import AuthResponse, UserOut
from api.services import auth_service


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_FAKE_USER = {
    "user_id": str(uuid4()),
    "auth_provider": "google",
    "provider_sub": "goog-sub-123",
    "email": "unit@example.com",
    "name": "Unit Tester",
    "avatar_url": None,
    "created_at": "2025-01-01T00:00:00+00:00",
}

_FAKE_SESSION = {
    "session_id": str(uuid4()),
    "user_id": _FAKE_USER["user_id"],
    "expires_at": "2099-01-01T00:00:00+00:00",
    "created_at": "2025-01-01T00:00:00+00:00",
}


class FakeUserStore:
    def __init__(self, *, user=None):
        self._user = user
        self.deleted_ids: list[str] = []

    def upsert(self, user):
        return {**user, "created_at": "2025-01-01T00:00:00+00:00"}

    def get_by_id(self, user_id):
        if self._user and self._user["user_id"] == user_id:
            return dict(self._user)
        return None

    def delete(self, user_id):
        self.deleted_ids.append(user_id)
        if self._user and self._user["user_id"] == user_id:
            return True
        return False


class FakeSessionStore:
    def __init__(self, *, session=None):
        self._session = session
        self.deleted_ids: list[str] = []

    def create(self, session):
        return dict(session)

    def get(self, session_id):
        if self._session and self._session["session_id"] == session_id:
            return dict(self._session)
        return None

    def delete(self, session_id):
        self.deleted_ids.append(session_id)

    def delete_expired(self):
        pass


@pytest.fixture
def mock_response():
    return MagicMock()


# ------------------------------------------------------------------
# validate_session
# ------------------------------------------------------------------

def test_validate_session_none():
    with pytest.raises(Unauthorized, match="Authentication required"):
        auth_service.validate_session(None)


def test_validate_session_not_found(monkeypatch):
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    with pytest.raises(Unauthorized, match="expired or invalid"):
        auth_service.validate_session("nonexistent-id")


def test_validate_session_valid(monkeypatch):
    store = FakeSessionStore(session=_FAKE_SESSION)
    monkeypatch.setattr(auth_service, "session_store", store)
    user_id = auth_service.validate_session(_FAKE_SESSION["session_id"])
    assert user_id == _FAKE_USER["user_id"]


# ------------------------------------------------------------------
# google_login
# ------------------------------------------------------------------

def test_google_login_invalid_token(monkeypatch, mock_response):
    def _raise(*_a, **_kw):
        raise AuthTokenInvalidError("bad token")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(Unauthorized, match="bad token"):
        auth_service.google_login("bad-token", mock_response)


def test_google_login_network_error(monkeypatch, mock_response):
    def _raise(*_a, **_kw):
        raise AuthTokenNetworkError("connection refused")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(ExternalAPIError, match="connection refused"):
        auth_service.google_login("some-token", mock_response)


def test_google_login_missing_email(monkeypatch, mock_response):
    fake_id_info = {"sub": "some-sub", "name": "No Email"}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(Unauthorized, match="missing 'email' claim"):
        auth_service.google_login("valid-token", mock_response)


def test_google_login_rejects_unverified_email(monkeypatch, mock_response):
    """email_verified=False → Unauthorized (the account email is not verified)."""
    fake_id_info = {"sub": "unv", "email": "unverified@example.com", "name": "Unv", "email_verified": False}
    monkeypatch.setattr(
        auth_service, "verify_google_token", lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(Unauthorized, match="email is not verified"):
        auth_service.google_login("valid-token", mock_response)


def test_google_login_rejects_when_email_verified_absent(monkeypatch, mock_response):
    """Missing email_verified claim → Unauthorized (treated as not verified)."""
    fake_id_info = {"sub": "abs", "email": "absent@example.com", "name": "Absent"}
    monkeypatch.setattr(
        auth_service, "verify_google_token", lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(Unauthorized, match="email is not verified"):
        auth_service.google_login("valid-token", mock_response)


def test_google_login_new_user(monkeypatch, mock_response):
    fake_id_info = {"sub": "new-sub", "email": "new@example.com", "name": "New", "email_verified": True}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    result = auth_service.google_login("valid-token", mock_response)
    assert isinstance(result, AuthResponse)
    assert result.user.email == "new@example.com"
    mock_response.set_cookie.assert_called_once()


def test_google_login_existing_user(monkeypatch, mock_response):
    fake_id_info = {"sub": _FAKE_USER["provider_sub"], "email": "updated@example.com", "name": "Updated", "email_verified": True}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore(user=_FAKE_USER))
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    result = auth_service.google_login("valid-token", mock_response)
    assert isinstance(result, AuthResponse)
    assert result.user.email == "updated@example.com"


def test_google_login_cookie_honours_secure_and_samesite_settings(monkeypatch, mock_response):
    fake_id_info = {"sub": "s", "email": "a@b.com", "name": "X", "email_verified": True}
    monkeypatch.setattr(
        auth_service, "verify_google_token", lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")

    auth_service.google_login("valid-token", mock_response)

    kwargs = mock_response.set_cookie.call_args.kwargs
    assert kwargs["secure"] is True
    assert kwargs["samesite"] == "strict"
    assert kwargs["httponly"] is True


# ------------------------------------------------------------------
# microsoft_login
# ------------------------------------------------------------------


class RecordingUserStore(FakeUserStore):
    """FakeUserStore that records the dict passed to ``upsert``."""

    def __init__(self, *, user=None):
        super().__init__(user=user)
        self.upserts: list[dict] = []

    def upsert(self, user):
        self.upserts.append(dict(user))
        return super().upsert(user)


def _ms_env(monkeypatch, *, microsoft="ms-cid"):
    """Set GOOGLE_CLIENT_ID (always required by _load_auth_settings) and MS id."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    if microsoft is None:
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    else:
        monkeypatch.setenv("MICROSOFT_CLIENT_ID", microsoft)


def test_microsoft_login_not_configured_raises_env_var_error(monkeypatch, mock_response):
    """MICROSOFT_CLIENT_ID absent → EnvVarError before verify_microsoft_token is called."""
    _ms_env(monkeypatch, microsoft=None)

    def _must_not_run(*_a, **_kw):
        raise AssertionError("verify_microsoft_token must not be called when unconfigured")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _must_not_run)

    with pytest.raises(
        EnvVarError,
        match="Microsoft login is not configured: MICROSOFT_CLIENT_ID is missing.",
    ):
        auth_service.microsoft_login("any-token", mock_response)


def test_microsoft_login_invalid_token(monkeypatch, mock_response):
    """AuthTokenInvalidError from verify → Unauthorized (translated)."""
    def _raise(*_a, **_kw):
        raise AuthTokenInvalidError("bad ms token")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _raise)
    _ms_env(monkeypatch)

    with pytest.raises(Unauthorized, match="bad ms token"):
        auth_service.microsoft_login("bad-token", mock_response)


def test_microsoft_login_network_error(monkeypatch, mock_response):
    """AuthTokenNetworkError from verify → ExternalAPIError (502, not 401)."""
    def _raise(*_a, **_kw):
        raise AuthTokenNetworkError("JWKS unreachable")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _raise)
    _ms_env(monkeypatch)

    with pytest.raises(ExternalAPIError, match="JWKS unreachable"):
        auth_service.microsoft_login("some-token", mock_response)


def test_microsoft_login_verify_unexpected_error(monkeypatch, mock_response):
    """A non-AuthError escaping verify → Unauthorized fallback."""
    def _raise(*_a, **_kw):
        raise RuntimeError("TLS handshake failed")

    monkeypatch.setattr(auth_service, "verify_microsoft_token", _raise)
    _ms_env(monkeypatch)

    with pytest.raises(Unauthorized, match="Microsoft token verification failed unexpectedly"):
        auth_service.microsoft_login("some-token", mock_response)


def test_microsoft_login_missing_sub(monkeypatch, mock_response):
    """Claims without 'sub' → Unauthorized."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"email": "x@contoso.com", "tid": "t"},
    )
    _ms_env(monkeypatch)

    with pytest.raises(Unauthorized, match="Microsoft token missing 'sub' claim"):
        auth_service.microsoft_login("valid-token", mock_response)


def test_microsoft_login_missing_email_and_preferred_username(monkeypatch, mock_response):
    """Neither 'email' nor 'preferred_username' → Unauthorized."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "name": "No Email"},
    )
    _ms_env(monkeypatch)

    with pytest.raises(
        Unauthorized,
        match="Microsoft token missing both 'email' and 'preferred_username' claims",
    ):
        auth_service.microsoft_login("valid-token", mock_response)


def test_microsoft_login_uses_email_when_present(monkeypatch, mock_response):
    """email present → it is used as the user email."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "primary@contoso.com", "name": "X"},
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    result = auth_service.microsoft_login("valid-token", mock_response)
    assert isinstance(result, AuthResponse)
    assert result.user.email == "primary@contoso.com"
    assert result.message == "Login successful."
    mock_response.set_cookie.assert_called_once()


def test_microsoft_login_falls_back_to_preferred_username(monkeypatch, mock_response):
    """email absent but preferred_username present → preferred_username used as email."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "preferred_username": "upn@contoso.com", "name": "X"},
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    result = auth_service.microsoft_login("valid-token", mock_response)
    assert result.user.email == "upn@contoso.com"


def test_microsoft_login_accepts_claims_without_email_verified(monkeypatch, mock_response):
    """email_verified is NOT checked (deliberate asymmetry with Google)."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "noev@contoso.com", "name": "X"},
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    # No email_verified key at all → still a successful login.
    result = auth_service.microsoft_login("valid-token", mock_response)
    assert result.user.email == "noev@contoso.com"


def test_microsoft_login_upsert_uses_microsoft_provider_and_sub(monkeypatch, mock_response):
    """The upsert dict carries auth_provider='microsoft' and provider_sub=sub."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "entra-sub-999", "email": "u@contoso.com", "name": "U"},
    )
    store = RecordingUserStore()
    monkeypatch.setattr(auth_service, "user_store", store)
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    auth_service.microsoft_login("valid-token", mock_response)

    assert len(store.upserts) == 1
    upserted = store.upserts[0]
    assert upserted["auth_provider"] == "microsoft"
    assert upserted["provider_sub"] == "entra-sub-999"
    assert upserted["email"] == "u@contoso.com"
    # Entra normally emits no picture → avatar_url None.
    assert upserted["avatar_url"] is None


def test_microsoft_login_upsert_database_error_translated(monkeypatch, mock_response):
    """DatabaseError from user_store.upsert → translated ApiError."""
    class FailingUserStore(FakeUserStore):
        def upsert(self, user):
            raise QueryError("DB fail")

    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "u@contoso.com", "name": "U"},
    )
    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    with pytest.raises(ApiError):
        auth_service.microsoft_login("valid-token", mock_response)


def test_microsoft_login_upsert_unexpected_error(monkeypatch, mock_response):
    """RuntimeError from user_store.upsert → UserOperationError."""
    class FailingUserStore(FakeUserStore):
        def upsert(self, user):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "u@contoso.com", "name": "U"},
    )
    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)

    with pytest.raises(UserOperationError, match="Failed to upsert user during Microsoft login"):
        auth_service.microsoft_login("valid-token", mock_response)


def test_microsoft_login_session_create_unexpected_error(monkeypatch, mock_response):
    """RuntimeError from session_store.create → SessionOperationError."""
    class FailingSessionStore(FakeSessionStore):
        def create(self, session):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "u@contoso.com", "name": "U"},
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    _ms_env(monkeypatch)

    with pytest.raises(SessionOperationError, match="Failed to create session during Microsoft login"):
        auth_service.microsoft_login("valid-token", mock_response)


def test_microsoft_login_cookie_honours_secure_and_samesite_settings(monkeypatch, mock_response):
    """The session cookie reflects the cookie_secure / cookie_samesite settings."""
    monkeypatch.setattr(
        auth_service, "verify_microsoft_token",
        lambda *_a, **_kw: {"sub": "ms-sub", "email": "u@contoso.com", "name": "U"},
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    _ms_env(monkeypatch)
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")

    auth_service.microsoft_login("valid-token", mock_response)

    kwargs = mock_response.set_cookie.call_args.kwargs
    assert kwargs["secure"] is True
    assert kwargs["samesite"] == "strict"
    assert kwargs["httponly"] is True


# ------------------------------------------------------------------
# logout
# ------------------------------------------------------------------

def test_logout_deletes_session(monkeypatch, mock_response):
    store = FakeSessionStore(session=_FAKE_SESSION)
    monkeypatch.setattr(auth_service, "session_store", store)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    result = auth_service.logout(_FAKE_SESSION["session_id"], mock_response)
    assert _FAKE_SESSION["session_id"] in store.deleted_ids
    assert result == {"status": "logged_out"}
    mock_response.delete_cookie.assert_called_once()


def test_logout_none_is_noop(monkeypatch, mock_response):
    store = FakeSessionStore()
    monkeypatch.setattr(auth_service, "session_store", store)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    result = auth_service.logout(None, mock_response)
    assert store.deleted_ids == []
    assert result == {"status": "logged_out"}


# ------------------------------------------------------------------
# get_current_user
# ------------------------------------------------------------------

def test_get_current_user_not_found(monkeypatch):
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    with pytest.raises(UserNotFound, match="User not found"):
        auth_service.get_current_user("nonexistent")


def test_get_current_user_found(monkeypatch):
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore(user=_FAKE_USER))
    user = auth_service.get_current_user(_FAKE_USER["user_id"])
    assert isinstance(user, UserOut)
    assert user.email == _FAKE_USER["email"]


# ------------------------------------------------------------------
# delete_account
# ------------------------------------------------------------------

def test_delete_account_success(monkeypatch, mock_response):
    store = FakeUserStore(user=_FAKE_USER)
    monkeypatch.setattr(auth_service, "user_store", store)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    result = auth_service.delete_account(_FAKE_USER["user_id"], mock_response)
    assert result == {"status": "account_deleted"}
    assert _FAKE_USER["user_id"] in store.deleted_ids
    mock_response.delete_cookie.assert_called_once()


def test_delete_account_not_found(monkeypatch, mock_response):
    store = FakeUserStore()
    monkeypatch.setattr(auth_service, "user_store", store)
    with pytest.raises(UserNotFound, match="User not found"):
        auth_service.delete_account("nonexistent", mock_response)


# ------------------------------------------------------------------
# Error handling hardening — except Exception fallbacks
# ------------------------------------------------------------------

def test_google_login_settings_unexpected_error(monkeypatch, mock_response):
    """_load_auth_settings except Exception → EnvVarError."""
    def _raise():
        raise RuntimeError("config file missing")

    monkeypatch.setattr(auth_service, "get_auth_settings", _raise)

    with pytest.raises(EnvVarError, match="Failed to load auth settings"):
        auth_service.google_login("some-token", mock_response)


def test_google_login_verify_unexpected_error(monkeypatch, mock_response):
    """verify_google_token except Exception → Unauthorized."""
    def _raise(*_a, **_kw):
        raise RuntimeError("TLS handshake failed")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(Unauthorized, match="Token verification failed"):
        auth_service.google_login("some-token", mock_response)


def test_google_login_succeeds_when_cleanup_fails(monkeypatch, mock_response):
    """_cleanup_expired_sessions failure is silent — login still returns AuthResponse."""
    class FailingSessionStore(FakeSessionStore):
        def delete_expired(self):
            raise RuntimeError("cleanup boom")

    fake_id_info = {"sub": "cleanup-sub", "email": "cleanup@example.com", "name": "Cleanup", "email_verified": True}
    monkeypatch.setattr(
        auth_service, "verify_google_token",
        lambda *_a, **_kw: fake_id_info,
    )
    monkeypatch.setattr(auth_service, "user_store", FakeUserStore())
    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    result = auth_service.google_login("valid-token", mock_response)
    assert isinstance(result, AuthResponse)
    assert result.user.email == "cleanup@example.com"


def test_google_login_auth_base_error(monkeypatch, mock_response):
    """AuthError base class from verify → translated via _AUTH_TO_API_MAP → ApiError."""
    def _raise(*_a, **_kw):
        raise AuthError("generic auth failure")

    monkeypatch.setattr(auth_service, "verify_google_token", _raise)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")

    with pytest.raises(ApiError, match="generic auth failure"):
        auth_service.google_login("some-token", mock_response)


# ------------------------------------------------------------------
# validate_session — DatabaseError + Exception fallbacks
# ------------------------------------------------------------------

def test_validate_session_database_error_raises_translated(monkeypatch):
    """DatabaseError from session_store.get → translated API error."""
    class FailingSessionStore(FakeSessionStore):
        def get(self, session_id):
            raise QueryError("DB fail")

    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    with pytest.raises(ApiError):
        auth_service.validate_session("some-session-id")


def test_validate_session_unexpected_error_raises_api_error(monkeypatch):
    """RuntimeError from session_store.get → ApiError."""
    class FailingSessionStore(FakeSessionStore):
        def get(self, session_id):
            raise RuntimeError("unexpected")

    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    with pytest.raises(ApiError, match="Failed to validate session"):
        auth_service.validate_session("some-session-id")


# ------------------------------------------------------------------
# get_current_user — DatabaseError + Exception fallbacks
# ------------------------------------------------------------------

def test_get_current_user_database_error_raises_translated(monkeypatch):
    """DatabaseError from user_store.get_by_id → translated API error."""
    class FailingUserStore(FakeUserStore):
        def get_by_id(self, user_id):
            raise QueryError("DB fail")

    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    with pytest.raises(ApiError):
        auth_service.get_current_user("some-user-id")


def test_get_current_user_unexpected_error_raises_api_error(monkeypatch):
    """RuntimeError from user_store.get_by_id → ApiError."""
    class FailingUserStore(FakeUserStore):
        def get_by_id(self, user_id):
            raise RuntimeError("unexpected")

    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    with pytest.raises(ApiError, match="Failed to look up current user"):
        auth_service.get_current_user("some-user-id")


# ------------------------------------------------------------------
# dev_login — three-state guard + happy path + error translation
# ------------------------------------------------------------------


class FakeUserStoreWithEmail(FakeUserStore):
    """Extends FakeUserStore with email lookup for dev_login tests."""

    def __init__(self, *, user=None):
        super().__init__(user=user)
        self.email_lookups: list[str] = []

    def get_by_email(self, email):
        self.email_lookups.append(email)
        if self._user and self._user["email"] == email:
            return dict(self._user)
        return None


def _enable_dev_login(monkeypatch, *, email="dev@example.com"):
    """Activate DEV_LOGIN_ENABLED + DEV_LOGIN_EMAIL for happy-path tests."""
    monkeypatch.setenv("DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DEV_LOGIN_EMAIL", email)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")  # needed by _load_auth_settings


def test_dev_login_disabled_when_env_unset(monkeypatch, mock_response):
    """DEV_LOGIN_ENABLED unset → DevLoginDisabled (503)."""
    monkeypatch.delenv("DEV_LOGIN_ENABLED", raising=False)
    with pytest.raises(DevLoginDisabled, match="is not truthy"):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_disabled_when_env_is_false(monkeypatch, mock_response):
    """DEV_LOGIN_ENABLED=false → DevLoginDisabled."""
    monkeypatch.setenv("DEV_LOGIN_ENABLED", "false")
    with pytest.raises(DevLoginDisabled):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_refused_when_client_host_not_trusted(monkeypatch, mock_response):
    """client_host outside the trusted set → DevLoginNotLocalhost (403) with client_host in detail."""
    _enable_dev_login(monkeypatch)
    with pytest.raises(DevLoginNotLocalhost) as exc_info:
        auth_service.dev_login(mock_response, "10.0.0.5")
    assert exc_info.value.detail == {"client_host": "10.0.0.5"}


def test_dev_login_refused_when_client_host_is_none(monkeypatch, mock_response):
    """client_host=None (missing request.client) → DevLoginNotLocalhost."""
    _enable_dev_login(monkeypatch)
    with pytest.raises(DevLoginNotLocalhost):
        auth_service.dev_login(mock_response, None)


def test_dev_login_trusted_hosts_env_override(monkeypatch, mock_response):
    """DEV_LOGIN_TRUSTED_HOSTS env var widens the allowlist."""
    _enable_dev_login(monkeypatch)
    monkeypatch.setenv("DEV_LOGIN_TRUSTED_HOSTS", "10.0.0.5,127.0.0.1")
    monkeypatch.setattr(auth_service, "user_store", FakeUserStoreWithEmail(user={
        **_FAKE_USER, "email": "dev@example.com",
    }))
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())
    # 10.0.0.5 is now trusted; should reach the happy path.
    result = auth_service.dev_login(mock_response, "10.0.0.5")
    assert isinstance(result, AuthResponse)


def test_dev_login_missing_email_env(monkeypatch, mock_response):
    """DEV_LOGIN_ENABLED=true but DEV_LOGIN_EMAIL unset → EnvVarError (500)."""
    monkeypatch.setenv("DEV_LOGIN_ENABLED", "true")
    monkeypatch.delenv("DEV_LOGIN_EMAIL", raising=False)
    with pytest.raises(EnvVarError, match="DEV_LOGIN_EMAIL"):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_user_not_in_db(monkeypatch, mock_response):
    """No row matching the email → UserNotFound (404) with detail."""
    _enable_dev_login(monkeypatch, email="ghost@example.com")
    monkeypatch.setattr(auth_service, "user_store", FakeUserStoreWithEmail())  # empty store
    with pytest.raises(UserNotFound) as exc_info:
        auth_service.dev_login(mock_response, "127.0.0.1")
    assert exc_info.value.detail == {"email": "ghost@example.com"}


def test_dev_login_happy_path(monkeypatch, mock_response):
    """Existing user → AuthResponse + session cookie set."""
    _enable_dev_login(monkeypatch, email="amulas14@example.com")
    user = {**_FAKE_USER, "email": "amulas14@example.com"}
    store = FakeUserStoreWithEmail(user=user)
    monkeypatch.setattr(auth_service, "user_store", store)
    monkeypatch.setattr(auth_service, "session_store", FakeSessionStore())

    result = auth_service.dev_login(mock_response, "127.0.0.1")

    assert isinstance(result, AuthResponse)
    assert result.user.email == "amulas14@example.com"
    assert result.message == "Dev login successful."
    assert store.email_lookups == ["amulas14@example.com"]
    mock_response.set_cookie.assert_called_once()


def test_dev_login_database_error_on_user_lookup(monkeypatch, mock_response):
    """DatabaseError from user_store.get_by_email → translated ApiError."""
    _enable_dev_login(monkeypatch)

    class FailingUserStore(FakeUserStoreWithEmail):
        def get_by_email(self, email):
            raise QueryError("DB fail")

    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    with pytest.raises(ApiError):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_unexpected_error_on_user_lookup(monkeypatch, mock_response):
    """RuntimeError from user_store.get_by_email → UserOperationError."""
    _enable_dev_login(monkeypatch)

    class FailingUserStore(FakeUserStoreWithEmail):
        def get_by_email(self, email):
            raise RuntimeError("boom")

    monkeypatch.setattr(auth_service, "user_store", FailingUserStore())
    with pytest.raises(UserOperationError, match="Failed to look up dev login user"):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_database_error_on_session_create(monkeypatch, mock_response):
    """DatabaseError from session_store.create → translated ApiError."""
    _enable_dev_login(monkeypatch)
    monkeypatch.setattr(
        auth_service, "user_store", FakeUserStoreWithEmail(user={
            **_FAKE_USER, "email": "dev@example.com",
        }),
    )

    class FailingSessionStore(FakeSessionStore):
        def create(self, session):
            raise QueryError("DB fail")

    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    with pytest.raises(ApiError):
        auth_service.dev_login(mock_response, "127.0.0.1")


def test_dev_login_unexpected_error_on_session_create(monkeypatch, mock_response):
    """RuntimeError from session_store.create → SessionOperationError."""
    _enable_dev_login(monkeypatch)
    monkeypatch.setattr(
        auth_service, "user_store", FakeUserStoreWithEmail(user={
            **_FAKE_USER, "email": "dev@example.com",
        }),
    )

    class FailingSessionStore(FakeSessionStore):
        def create(self, session):
            raise RuntimeError("boom")

    monkeypatch.setattr(auth_service, "session_store", FailingSessionStore())
    with pytest.raises(SessionOperationError, match="Failed to create session during dev login"):
        auth_service.dev_login(mock_response, "127.0.0.1")
