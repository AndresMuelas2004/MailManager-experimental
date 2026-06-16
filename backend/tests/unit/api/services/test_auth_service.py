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
    "google_sub": "goog-sub-123",
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
    fake_id_info = {"sub": _FAKE_USER["google_sub"], "email": "updated@example.com", "name": "Updated", "email_verified": True}
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
