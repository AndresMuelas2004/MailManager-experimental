"""
Service layer for authentication operations.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Response

from auth import (
    AuthError,
    AuthSettings,
    get_auth_settings,
    verify_google_token,
)

from database import DatabaseError, session_store, user_store
from api.errors.exceptions import (
    DevLoginDisabled,
    DevLoginNotLocalhost,
    EnvVarError,
    SessionOperationError,
    Unauthorized,
    UserNotFound,
    UserOperationError,
)
from api.schemas.auth import AuthResponse, UserOut
from api.services.services_helpers import translate_auth_error, translate_database_error

logger = logging.getLogger(__name__)


_DEV_LOGIN_ENABLED_ENV_VAR = "DEV_LOGIN_ENABLED"
_DEV_LOGIN_EMAIL_ENV_VAR = "DEV_LOGIN_EMAIL"
_DEV_LOGIN_TRUSTED_HOSTS_ENV_VAR = "DEV_LOGIN_TRUSTED_HOSTS"
_DEFAULT_TRUSTED_HOSTS = ("127.0.0.1", "::1", "localhost")
_DEV_LOGIN_TRUE_VALUES = {"1", "true", "yes", "on"}


def _load_auth_settings() -> AuthSettings:
    """Load auth settings, translating auth errors to API errors."""
    try:
        return get_auth_settings()
    except AuthError as exc:
        raise translate_auth_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected auth settings error (%s): %s", type(exc).__name__, exc)
        raise EnvVarError("Failed to load auth settings.") from exc


def _set_session_cookie(response: Response, session_id: str, settings: AuthSettings) -> None:
    """Set the HttpOnly session cookie on *response*."""
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.session_lifetime_days * 86400,
    )


def _clear_session_cookie(response: Response, settings: AuthSettings) -> None:
    """Clear the session cookie on *response*."""
    response.delete_cookie(
        key="session_id",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def google_login(raw_id_token: str, response: Response) -> AuthResponse:
    """
    Verify a Google id_token, upsert the user, create a session,
    and set the session cookie on *response*.

    Returns an ``AuthResponse`` containing the user and a message.
    """
    settings = _load_auth_settings()
    try:
        id_info = verify_google_token(raw_id_token, settings.google_client_id)
    except AuthError as exc:
        logger.debug("Google token verification failed: %s", exc)
        raise translate_auth_error(exc) from exc
    except Exception as exc:
        logger.warning("Google token verification unexpected error (%s): %s", type(exc).__name__, exc)
        raise Unauthorized("Token verification failed.") from exc

    google_sub = id_info.get("sub")
    if not google_sub:
        raise Unauthorized("Token missing 'sub' claim.")

    email = id_info.get("email", "")
    if not email:
        raise Unauthorized("Token missing 'email' claim.")

    if id_info.get("email_verified") is not True:
        raise Unauthorized("Google login rejected: the account email is not verified.")

    # Only used for new users; the UPSERT returns the existing user_id for returning users.
    user_id = str(uuid4())
    try:
        user = user_store.upsert({
            "user_id": user_id,
            "google_sub": google_sub,
            "email": email,
            "name": id_info.get("name"),
            "avatar_url": id_info.get("picture"),
        })
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected user upsert error (%s): %s", type(exc).__name__, exc)
        raise UserOperationError("Failed to upsert user during Google login.") from exc

    session_id = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.session_lifetime_days)
    try:
        session_store.create({
            "session_id": session_id,
            "user_id": user["user_id"],
            "expires_at": expires_at.isoformat(),
        })
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected session creation error (%s): %s", type(exc).__name__, exc)
        raise SessionOperationError("Failed to create session during Google login.") from exc

    _set_session_cookie(response, session_id, settings)
    _cleanup_expired_sessions()
    return AuthResponse(user=UserOut(**user), message="Login successful.")


def validate_session(session_id: str | None) -> str:
    """
    Validate a session cookie and return the user_id.

    Raises ``Unauthorized`` when session_id is absent, invalid, or expired.
    """
    if not session_id:
        raise Unauthorized("Authentication required.")
    try:
        session = session_store.get(session_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected session validation error (%s): %s", type(exc).__name__, exc)
        raise SessionOperationError("Failed to validate session.") from exc
    if session is None:
        raise Unauthorized("Session expired or invalid.")
    return session["user_id"]


def logout(session_id: str | None, response: Response) -> dict[str, str]:
    """
    Delete the session identified by *session_id* and clear the cookie.
    """
    if session_id:
        try:
            session_store.delete(session_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning("Unexpected session deletion error (%s): %s", type(exc).__name__, exc)
            raise SessionOperationError("Failed to delete session during logout.") from exc
    settings = _load_auth_settings()
    _clear_session_cookie(response, settings)
    return {"status": "logged_out"}


def delete_account(user_id: str, response: Response) -> dict[str, str]:
    """
    Delete the user and all associated data (CASCADE handles mailboxes,
    accounts, tokens, and sessions), then clear the session cookie.

    Raises ``UserNotFound`` when the user does not exist.
    """
    try:
        deleted = user_store.delete(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected user deletion error (%s): %s", type(exc).__name__, exc)
        raise UserOperationError("Failed to delete user account.") from exc
    if not deleted:
        raise UserNotFound("User not found while deleting account.", {"user_id": user_id})
    settings = _load_auth_settings()
    _clear_session_cookie(response, settings)
    return {"status": "account_deleted"}


def _cleanup_expired_sessions() -> None:
    """Best-effort removal of expired sessions. Failures are logged, not raised."""
    try:
        session_store.delete_expired()
    except Exception:
        logger.warning("Expired session cleanup failed.", exc_info=True)


def _is_dev_login_enabled() -> bool:
    raw = os.getenv(_DEV_LOGIN_ENABLED_ENV_VAR, "")
    return raw.strip().lower() in _DEV_LOGIN_TRUE_VALUES


def _trusted_dev_login_hosts() -> set[str]:
    raw = os.getenv(_DEV_LOGIN_TRUSTED_HOSTS_ENV_VAR, "").strip()
    if not raw:
        return set(_DEFAULT_TRUSTED_HOSTS)
    return {h.strip() for h in raw.split(",") if h.strip()}


def dev_login(response: Response, client_host: str | None) -> AuthResponse:
    """Dev-only backdoor: mint a session for the user named by
    ``DEV_LOGIN_EMAIL``.

    Three-state guard (mirrors ``attachments_service.purge_expired_attachments``):

    1. ``DEV_LOGIN_ENABLED`` is not truthy → ``DevLoginDisabled`` (503
       ``dev_login_disabled``). The deploy was not configured for this
       operation; treat as a no-op rather than 403, so misuse does not
       look like a credential issue.
    2. ``client_host`` not in ``DEV_LOGIN_TRUSTED_HOSTS`` (default
       ``127.0.0.1``, ``::1``, ``localhost``) → ``DevLoginNotLocalhost``
       (403 ``dev_login_not_localhost``).
    3. ``DEV_LOGIN_EMAIL`` is unset → ``EnvVarError`` (500
       ``env_var_error``). At this point we know the operator
       intentionally enabled the endpoint, so a missing email is a
       config bug, not a security guard.

    On success: emits the SAME opaque session cookie as ``google_login``
    (``session_id``, HttpOnly, ``SameSite`` and ``secure`` per
    settings) and returns ``AuthResponse``.
    """
    if not _is_dev_login_enabled():
        raise DevLoginDisabled(
            f"Dev login endpoint disabled: {_DEV_LOGIN_ENABLED_ENV_VAR} is not truthy."
        )
    if client_host not in _trusted_dev_login_hosts():
        raise DevLoginNotLocalhost(
            "Dev login refused: request origin is not in the trusted hosts list.",
            {"client_host": client_host},
        )
    email = os.getenv(_DEV_LOGIN_EMAIL_ENV_VAR, "").strip()
    if not email:
        raise EnvVarError(
            f"Dev login email not configured: {_DEV_LOGIN_EMAIL_ENV_VAR} is missing."
        )
    settings = _load_auth_settings()
    try:
        user = user_store.get_by_email(email)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected dev-login user lookup error (%s): %s",
            type(exc).__name__, exc,
        )
        raise UserOperationError("Failed to look up dev login user.") from exc
    if user is None:
        raise UserNotFound(
            "User not found while resolving dev login email.",
            {"email": email},
        )

    session_id = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.session_lifetime_days)
    try:
        session_store.create({
            "session_id": session_id,
            "user_id": user["user_id"],
            "expires_at": expires_at.isoformat(),
        })
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected session creation error during dev login (%s): %s",
            type(exc).__name__, exc,
        )
        raise SessionOperationError("Failed to create session during dev login.") from exc

    _set_session_cookie(response, session_id, settings)
    _cleanup_expired_sessions()
    return AuthResponse(user=UserOut(**user), message="Dev login successful.")


def get_current_user(user_id: str) -> UserOut:
    """
    Fetch the user record for *user_id*.

    Raises ``UserNotFound`` when the user no longer exists.
    """
    try:
        user = user_store.get_by_id(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected user lookup error (%s): %s", type(exc).__name__, exc)
        raise UserOperationError("Failed to look up current user.") from exc
    if user is None:
        raise UserNotFound("User not found while fetching current user.", {"user_id": user_id})
    return UserOut(**user)
