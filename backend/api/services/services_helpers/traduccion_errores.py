"""Traduccion de errores de capa (auth / core / database / connect) a subclases de ApiError."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from auth import (
    AuthError,
    AuthSettingsError,
    AuthTokenError,
    AuthTokenInvalidError,
    AuthTokenNetworkError,
    AuthTokenProviderError,
)
from core.email import (
    CoreError,
    EmailAccountNotFoundError,
    EmailAccountRecordError,
    EmailAttachmentBlockedByProvider,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
    EmailAuthError,
    EmailConfigError,
    EmailDuplicateAccountLabelError,
    EmailExternalAPIError,
    EmailInvalidCredentialsDataError,
    EmailInvalidExpiryError,
    EmailInvalidTokenDataError,
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailNotAuthenticatedError,
    EmailProviderConfigError,
    EmailRecipientsMissingError,
    EmailRefreshFailedError,
    EmailReplyContextFetchError,
)
from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountMisconfigured,
    AccountNotConnected,
    AccountNotFound,
    ApiError,
    AppCredentialsInvalid,
    AppCredentialsMissing,
    AttachmentBlockedExtension,
    AttachmentProviderForbidden,
    AttachmentProviderUnavailable,
    AttachmentSendFailed,
    AttachmentTooLarge,
    AttachmentUnavailable,
    EmailReplyContextError,
    CredentialFileError,
    DatabaseConnectionError,
    DatabaseMigrationError,
    DatabaseQueryError,
    EnvVarError,
    ExternalAPIError,
    RecipientsMissing,
    TokenDecryptionError,
    TokenEncryptionError,
    TokenIntegrityError,
    Unauthorized,
)
from database import (
    ConnectionPoolError,
    CredentialReadError,
    DatabaseError,
    MigrationError,
    QueryError,
    SettingsError,
    TokenCryptoError,
    TokenDecryptError,
    TokenEncryptError,
    TokenValidationError,
    UnknownProviderError,
)


# ---------------------------------------------------------------------------
# Core → API error mapping (most specific first; evaluated with isinstance)
# ---------------------------------------------------------------------------

_CORE_TO_API_MAP: list[tuple[type[CoreError], type[ApiError]]] = [
    (EmailAccountNotFoundError, AccountNotFound),
    (EmailMissingTokenError, AccountNotConnected),
    (EmailMissingRefreshTokenError, AccountNotConnected),
    (EmailRefreshFailedError, AccountNotConnected),
    (EmailNotAuthenticatedError, AccountNotConnected),
    (EmailAuthError, AccountNotConnected),
    (EmailInvalidExpiryError, AccountMisconfigured),
    (EmailInvalidCredentialsDataError, AppCredentialsInvalid),
    (EmailInvalidTokenDataError, AccountMisconfigured),
    (EmailAccountRecordError, AccountMisconfigured),
    (EmailProviderConfigError, AccountMisconfigured),
    (EmailMissingAppCredentialsError, AppCredentialsMissing),
    (EmailDuplicateAccountLabelError, AccountMisconfigured),
    (EmailConfigError, AccountMisconfigured),
    (EmailRecipientsMissingError, RecipientsMissing),
    # Attachment errors — listed before EmailExternalAPIError so they
    # do not get caught by the generic external-API translation.
    # AttachmentDownloadFailed splits 502/503 by reason in
    # ``translate_core_error`` overrides below; the default here is
    # 503 (provider unavailable).
    (EmailAttachmentNotFound, AttachmentUnavailable),
    (EmailAttachmentBlockedByProvider, AttachmentBlockedExtension),
    (EmailAttachmentTooLargeForProvider, AttachmentTooLarge),
    (EmailAttachmentSendFailed, AttachmentSendFailed),
    (EmailAttachmentDownloadFailed, AttachmentProviderUnavailable),
    # Reply / forward context fetch failures map to the dedicated
    # ``EmailReplyContextError`` (HTTP 502). Listed before the generic
    # external API translation so it does not get caught by it.
    (EmailReplyContextFetchError, EmailReplyContextError),
    (EmailExternalAPIError, ExternalAPIError),
    (CoreError, ApiError),
]


# ---------------------------------------------------------------------------
# Database → API error mapping (most specific first; evaluated with isinstance)
# ---------------------------------------------------------------------------

_DB_TO_API_MAP: list[tuple[type[DatabaseError], type[ApiError]]] = [
    (ConnectionPoolError, DatabaseConnectionError),
    (QueryError, DatabaseQueryError),
    (MigrationError, DatabaseMigrationError),
    (SettingsError, EnvVarError),
    (TokenDecryptError, TokenDecryptionError),
    (TokenEncryptError, TokenEncryptionError),
    (TokenCryptoError, TokenDecryptionError),
    (TokenValidationError, TokenIntegrityError),
    (CredentialReadError, CredentialFileError),
    (UnknownProviderError, AccountMisconfigured),
    (DatabaseError, ApiError),
]


# ---------------------------------------------------------------------------
# Auth → API error mapping (most specific first; evaluated with isinstance)
# ---------------------------------------------------------------------------

_AUTH_TO_API_MAP: list[tuple[type[AuthError], type[ApiError]]] = [
    (AuthSettingsError, EnvVarError),
    (AuthTokenNetworkError, ExternalAPIError),
    (AuthTokenInvalidError, Unauthorized),
    (AuthTokenProviderError, Unauthorized),
    (AuthTokenError, Unauthorized),
    (AuthError, ApiError),
]


def translate_auth_error(
    exc: Exception,
    *,
    fallback: type[ApiError] = ApiError,
    context: dict[str, Any] | None = None,
) -> ApiError:
    """
    Translate an AuthError into the corresponding ApiError using the mapping.

    If *exc* is an AuthError subclass the first matching entry in
    ``_AUTH_TO_API_MAP`` is used.  Otherwise *fallback* is instantiated.
    """
    if isinstance(exc, AuthError):
        for auth_type, api_type in _AUTH_TO_API_MAP:
            if isinstance(exc, auth_type):
                detail = exc.detail if hasattr(exc, "detail") else {}
                if context:
                    detail = {**detail, **context}
                detail["auth_code"] = exc.code
                return api_type(exc.message, detail)
        # Unreachable when AuthError is in the map, but safe fallback.
        logger.warning("Unmapped AuthError subclass (%s): %s", type(exc).__name__, exc)
        return fallback(
            "Unexpected auth error.",
            {**(context or {}), "auth_code": exc.code},
        )
    logger.warning("Non-AuthError passed to translate_auth_error (%s): %s", type(exc).__name__, exc)
    return fallback("Unexpected auth error.", context or {})


def translate_core_error(
    exc: Exception,
    *,
    fallback: type[ApiError] = ApiError,
    context: dict[str, Any] | None = None,
) -> ApiError:
    """
    Translate a CoreError into the corresponding ApiError using the mapping.

    If *exc* is a CoreError subclass the first matching entry in
    ``_CORE_TO_API_MAP`` is used.  Otherwise *fallback* is instantiated.

    Attachment download failures carry a ``reason`` in ``detail`` that
    further splits the mapping: ``forbidden`` becomes
    :py:class:`AttachmentProviderForbidden` (HTTP 502), anything else
    keeps the default :py:class:`AttachmentProviderUnavailable` (HTTP
    503). This is the only place where ``detail`` influences the
    chosen ApiError class — the rest of the mapping is purely typed.
    """
    if isinstance(exc, CoreError):
        if isinstance(exc, EmailAttachmentDownloadFailed):
            reason = (exc.detail or {}).get("reason")
            api_class: type[ApiError] = (
                AttachmentProviderForbidden
                if reason == "forbidden"
                else AttachmentProviderUnavailable
            )
            detail = dict(exc.detail or {})
            if context:
                detail = {**detail, **context}
            detail["core_code"] = exc.code
            return api_class(exc.message, detail)
        for core_type, api_type in _CORE_TO_API_MAP:
            if isinstance(exc, core_type):
                detail = exc.detail if hasattr(exc, "detail") else {}
                if context:
                    detail = {**detail, **context}
                detail["core_code"] = exc.code
                return api_type(exc.message, detail)
        # Unreachable when CoreError is in the map, but safe fallback.
        logger.warning("Unmapped CoreError subclass (%s): %s", type(exc).__name__, exc)
        return fallback(
            "Unexpected core error.",
            {**(context or {}), "core_code": exc.code},
        )
    logger.warning("Non-CoreError passed to translate_core_error (%s): %s", type(exc).__name__, exc)
    return fallback("Unexpected error.", context or {})


def translate_database_error(
    exc: Exception,
    *,
    fallback: type[ApiError] = ApiError,
    context: dict[str, Any] | None = None,
) -> ApiError:
    """
    Translate a DatabaseError into the corresponding ApiError using the mapping.

    If *exc* is a DatabaseError subclass the first matching entry in
    ``_DB_TO_API_MAP`` is used.  Otherwise *fallback* is instantiated.
    """
    if isinstance(exc, DatabaseError):
        for db_type, api_type in _DB_TO_API_MAP:
            if isinstance(exc, db_type):
                detail = exc.detail if hasattr(exc, "detail") else {}
                if context:
                    detail = {**detail, **context}
                detail["db_code"] = exc.code
                return api_type(exc.message, detail)
        # Unreachable when DatabaseError is in the map, but safe fallback.
        logger.warning("Unmapped DatabaseError subclass (%s): %s", type(exc).__name__, exc)
        return fallback(
            "Unexpected database error.",
            {**(context or {}), "db_code": exc.code},
        )
    logger.warning("Non-DatabaseError passed to translate_database_error (%s): %s", type(exc).__name__, exc)
    return fallback("Unexpected database error.", context or {})



def translate_connect_error(
    exc: Exception,
    *,
    context: dict[str, Any] | None = None,
) -> ApiError:
    """
    Translate errors for the interactive account-connect flow.

    Unlike mailbox operations that require a pre-connected account (409),
    connect-time authentication failures should return 401.
    """
    if isinstance(exc, EmailAuthError):
        detail = exc.detail if hasattr(exc, "detail") else {}
        if context:
            detail = {**detail, **context}
        detail["core_code"] = exc.code
        return AccountConnectAuthError(exc.message, detail)
    return translate_core_error(exc, fallback=AccountConnectAuthError, context=context)


def is_auth_error(exc: Exception) -> bool:
    """
    Return True when the exception is a typed core authentication error.
    """
    return isinstance(exc, EmailAuthError)
