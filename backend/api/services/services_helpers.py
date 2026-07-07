"""
Shared helpers used across service modules.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from pydantic import SecretStr

from api.services.email_html_pipeline import prepare_email_html as sanitize_email_html
from api.services.outbound_html_pipeline import sanitize_outbound_html

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
    EmailManager,
    EmailMetadata,
    EmailMissingAppCredentialsError,
    EmailMissingRefreshTokenError,
    EmailMissingTokenError,
    EmailNotAuthenticatedError,
    EmailProviderConfigError,
    EmailRecipientsMissingError,
    EmailRefreshFailedError,
    EmailReplyContextFetchError,
    LabelUpdate,
    SpamMoveResult,
    format_content_disposition,
)

from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountMisconfigured,
    AccountNotConnected,
    AccountNotFound,
    AccountTokensLoadError,
    ApiError,
    AppCredentialsInvalid,
    AppCredentialsLoadError,
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
    Forbidden,
    MailboxLookupError,
    MailboxNotFound,
    RecipientsMissing,
    TokenDecryptionError,
    TokenEncryptionError,
    TokenIntegrityError,
    Unauthorized,
)
from api.schemas.email import AccountSyncFailure, EmailMetadataOut
from database import (
    account_store,
    email_content_store,
    ConnectionPoolError,
    CredentialReadError,
    DatabaseError,
    email_metadata_store,
    load_app_credentials,
    mailbox_store,
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


def ensure_mailbox_access(mailbox_id: str, user_id: str) -> dict[str, Any]:
    """
    Ensure the mailbox exists and the authenticated user owns it.

    Returns the mailbox record so callers can reuse it without a second fetch.
    """
    try:
        record = mailbox_store.get(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mailbox lookup error (%s): %s", type(exc).__name__, exc)
        raise MailboxLookupError("Failed to look up mailbox.") from exc
    if record is None:
        raise MailboxNotFound(f"Mailbox '{mailbox_id}' not found.")
    if record.get("owner_user_id") != user_id:
        raise Forbidden("You do not have access to this mailbox.")
    return record


def build_manager_for_accounts(accounts: Iterable[dict[str, Any]]) -> EmailManager:
    """
    Build an EmailManager and register all account records on it.
    """
    manager = EmailManager()
    for account in accounts:
        try:
            manager.add_account_record(account)
        except CoreError as exc:
            raise translate_core_error(exc, fallback=AccountMisconfigured) from exc
        except Exception as exc:
            logger.warning("Failed to register account in manager (%s): %s", type(exc).__name__, exc)
            raise AccountMisconfigured(
                "Failed to register account in manager."
            ) from exc
    return manager


def raise_on_silent_auth_errors(
    errors: dict[str, Exception],
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """
    Inspect the per-account errors collected during EmailManager.authenticate_all_silent().

    - Non-auth CoreErrors are translated via the centralized mapping and raised immediately.
    - Auth-related errors are accumulated and raised as a single AccountNotConnected.
    - Non-CoreError exceptions are raised through the fallback path.
    """
    if not errors:
        return

    auth_labels: list[str] = []
    reasons: dict[str, str] = {}
    for label, error in errors.items():
        if is_auth_error(error):
            auth_labels.append(label)
            reason = str(error).strip()
            if reason:
                reasons[label] = reason
        else:
            raise translate_core_error(error, fallback=fallback) from error

    if auth_labels:
        detail: dict[str, Any] = {"account_labels": auth_labels}
        if reasons:
            detail["reasons"] = reasons
        raise AccountNotConnected(
            "One or more accounts are not connected. Call /connect first.",
            detail,
        )


def is_auth_error(exc: Exception) -> bool:
    """
    Return True when the exception is a typed core authentication error.
    """
    return isinstance(exc, EmailAuthError)


def build_account_sync_failures(
    errors: dict[str, Exception],
    label_lookup: dict[str, tuple[str, str, str]],
) -> list[AccountSyncFailure]:
    """Turn the per-account ``{account_label: exception}`` failure map into
    :class:`AccountSyncFailure` rows WITHOUT raising.

    Used on the partial-success path of ``sync_email_metadata``: the healthy
    accounts have already persisted, so the failures (auth AND non-auth) must
    be reported inside the 200 response instead of aborting the call. This is
    the deliberate counterpart to :func:`raise_on_silent_auth_errors`, which
    raises immediately on the first non-auth error and cannot walk the whole
    map. ``label_lookup`` maps ``account_label -> (mailbox_id, account_id,
    provider)``; labels absent from it are skipped (mirrors the sync loop's
    own ``label_lookup.get`` guard). ``is_auth_error`` alone decides whether a
    row is ``"account_not_connected"`` (expired/revoked token) or
    ``"sync_failed"`` (any other failure).
    """
    failures: list[AccountSyncFailure] = []
    for label, error in errors.items():
        ids = label_lookup.get(label)
        if not ids:
            continue
        _mailbox_id, account_id, provider = ids
        reason = "account_not_connected" if is_auth_error(error) else "sync_failed"
        failures.append(AccountSyncFailure(
            account_id=account_id, provider=provider, reason=reason,
        ))
    return failures


def _wrap_secret(value: Any) -> Any:
    if value is None:
        return None
    return SecretStr(str(value))


def unwrap_secret(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def load_wrapped_app_credentials(provider: str) -> dict[str, Any]:
    """
    Load app credentials for *provider* and wrap the client_secret as SecretStr.
    """
    try:
        credentials = load_app_credentials(provider)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected credentials load error (%s): %s", type(exc).__name__, exc)
        raise AppCredentialsLoadError("Failed to load app credentials.") from exc
    payload = dict(credentials) if isinstance(credentials, dict) else {}
    if "client_secret" in payload:
        payload["client_secret"] = _wrap_secret(payload.get("client_secret"))
    return payload


def load_wrapped_account_tokens(
    mailbox_id: str, account_id: str, provider: str,
) -> dict[str, Any]:
    """
    Load account tokens for *provider* and wrap access/refresh tokens as SecretStr.
    """
    try:
        token_data = account_store.get_tokens(mailbox_id, account_id, provider)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected token load error (%s): %s", type(exc).__name__, exc)
        raise AccountTokensLoadError("Failed to load account tokens.") from exc
    payload = dict(token_data) if isinstance(token_data, dict) else {}
    if "access_token" in payload:
        payload["access_token"] = _wrap_secret(payload.get("access_token"))
    if "refresh_token" in payload:
        payload["refresh_token"] = _wrap_secret(payload.get("refresh_token"))
    return payload


# ---------------------------------------------------------------------------
# Email metadata persistence helpers
# ---------------------------------------------------------------------------


def persist_email_metadata_batch(
    account_id: str,
    metadata_list: list[EmailMetadata],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Persist email metadata to DB via batch upsert. Returns rows affected."""
    if not metadata_list:
        return 0
    rows = [
        (
            m.provider_message_id, account_id, m.thread_id, m.from_email,
            m.from_name, m.subject, m.received_at, m.is_read, m.box,
            m.to_email, m.to_name,
        )
        for m in metadata_list
    ]
    try:
        return email_metadata_store.upsert_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected metadata persist error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to persist email metadata.") from exc


def load_sync_cursors(
    label_lookup: dict[str, tuple[str, str, str]],
    *,
    fallback: type[ApiError] = ApiError,
) -> dict[str, str | None]:
    """Load the sync cursor for each account, keyed by account label.

    Batches the lookup per distinct mailbox (one query each) instead of one
    query per account, avoiding the N+1 round trips the previous per-account
    ``get_sync_cursor`` loop produced.
    """
    mailbox_ids = {mailbox_id for (mailbox_id, _aid, _provider) in label_lookup.values()}
    try:
        cursors_by_mailbox = {
            mailbox_id: account_store.get_sync_cursors_for_mailbox(mailbox_id)
            for mailbox_id in mailbox_ids
        }
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected sync cursor load error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to load sync cursor.") from exc

    cursors: dict[str, str | None] = {}
    for label, (mailbox_id, account_id, _provider) in label_lookup.items():
        cursors[label] = cursors_by_mailbox.get(mailbox_id, {}).get(account_id)
    return cursors


def delete_email_metadata_batch(
    account_id: str,
    message_ids: list[str],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Delete email metadata by provider_message_ids. Returns rows deleted."""
    if not message_ids:
        return 0
    try:
        return email_metadata_store.delete_batch_by_message_ids(account_id, message_ids)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected metadata delete error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to delete email metadata.") from exc


def update_email_metadata_labels_batch(
    account_id: str,
    label_updates: list[LabelUpdate],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Update only is_read and box for specific messages. Returns rows updated."""
    if not label_updates:
        return 0
    rows = [
        (lu.provider_message_id, account_id, lu.is_read, lu.box)
        for lu in label_updates
    ]
    try:
        return email_metadata_store.update_labels_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected metadata labels update error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to update email metadata labels.") from exc


def update_email_read_status_batch(
    account_id: str,
    message_ids: list[str],
    is_read: bool,
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Update only is_read for specific messages. Returns rows updated."""
    if not message_ids:
        return 0
    rows = [(mid, account_id, is_read) for mid in message_ids]
    try:
        return email_metadata_store.update_read_status_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected read status DB update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback("Failed to update email read status in database.") from exc


def update_email_spam_status_batch(
    account_id: str,
    results: list[SpamMoveResult],
    new_box: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Update provider_message_id and box for messages moved to/from spam. Returns rows updated."""
    if not results:
        return 0
    rows = [
        (r.old_id, account_id, r.new_id, new_box)
        for r in results
    ]
    try:
        return email_metadata_store.update_spam_status_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected spam status DB update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback("Failed to update email spam status in database.") from exc


def load_suspect_message_ids(
    account_id: str,
    bootstrap_ids: list[str],
    *,
    fallback: type[ApiError] = ApiError,
) -> list[str]:
    """Load stored provider_message_ids NOT present in ``bootstrap_ids``.

    The set-difference runs server-side (LIST_PROVIDER_MESSAGE_IDS_NOT_IN) so
    only the suspect rows cross the wire — used by ghost-email reconciliation
    instead of loading every stored id into memory to diff in Python.
    """
    try:
        return email_metadata_store.list_provider_message_ids_not_in(account_id, bootstrap_ids)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected suspect message IDs load error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to load suspect message IDs.") from exc


def get_trash_emails_by_ids(
    account_id: str,
    message_ids: list[str],
    *,
    fallback: type[ApiError] = ApiError,
) -> list[dict[str, Any]]:
    """Get emails in TRASH by their provider_message_ids."""
    if not message_ids:
        return []
    try:
        return email_metadata_store.get_trash_emails_by_ids(account_id, message_ids)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected get trash emails error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to get trash emails.") from exc


def mark_as_deleted_batch(
    account_id: str,
    message_ids: list[str],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Mark emails as DELETED in the database."""
    if not message_ids:
        return 0
    try:
        return email_metadata_store.mark_as_deleted_batch(account_id, message_ids)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mark as deleted error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to mark emails as deleted.") from exc


def restore_from_trash_batch(
    account_id: str,
    rows: list[tuple],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Restore emails from trash in the database."""
    if not rows:
        return 0
    try:
        return email_metadata_store.restore_from_trash_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected restore from trash error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to restore emails from trash.") from exc


def restore_from_trash_discovered_batch(
    account_id: str,
    rows: list[tuple],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Restore emails from trash with a discovered box in the database."""
    if not rows:
        return 0
    try:
        return email_metadata_store.restore_from_trash_discovered_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected restore discovered box error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to restore emails with discovered box.") from exc


def move_to_trash_batch(
    account_id: str,
    rows: list[tuple],
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Move emails to trash in the database."""
    if not rows:
        return 0
    try:
        return email_metadata_store.move_to_trash_batch(account_id, rows)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected move to trash error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to move emails to trash.") from exc


def update_sync_cursor(
    mailbox_id: str,
    account_id: str,
    cursor: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """Persist the new sync_cursor for an account."""
    try:
        account_store.update_sync_cursor(mailbox_id, account_id, cursor)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected sync cursor update error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to update sync cursor.") from exc


# ---------------------------------------------------------------------------
# Email content helpers
# ---------------------------------------------------------------------------

def get_email_content(
    account_id: str,
    provider_message_id: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> dict[str, Any] | None:
    """Read cached email content from DB. Returns dict or None."""
    try:
        return email_content_store.get(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected email content read error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to read email content from database.") from exc


def persist_email_content(
    account_id: str,
    provider_message_id: str,
    html_body: str | None,
    text_body: str | None,
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """Persist email content to DB. CAN raise — caller decides best-effort wrapping."""
    try:
        email_content_store.upsert(account_id, provider_message_id, html_body, text_body)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected email content persist error (%s): %s", type(exc).__name__, exc)
        raise fallback("Failed to persist email content.") from exc


def touch_email_content_last_accessed(
    account_id: str, provider_message_id: str,
) -> None:
    """Refresh the cached body's ``last_accessed_at`` (sliding TTL).

    Best-effort by design: a failure to bump the TTL must NEVER affect the
    content response — the body is already served from cache. Swallows and
    logs every error (does not re-raise). Called on a cache HIT.
    """
    try:
        email_content_store.touch_last_accessed(account_id, provider_message_id)
    except Exception as exc:
        logger.warning(
            "Failed to touch email content last_accessed (%s): %s",
            type(exc).__name__, exc,
        )


def purge_expired_email_content(account_ids: list[str]) -> int:
    """Evict cached bodies idle for 30+ days for the given accounts (sliding TTL).

    Best-effort: runs in the post-sync background task and must never raise.
    Returns the number of rows purged, or 0 on any error.
    """
    try:
        return email_content_store.purge_expired_for_accounts(account_ids)
    except Exception as exc:
        logger.warning(
            "Failed to purge expired email content (%s): %s",
            type(exc).__name__, exc,
        )
        return 0


def list_unread_recent_uncached(
    account_id: str,
    limit: int,
    *,
    fallback: type[ApiError] = ApiError,
) -> list[str]:
    """List the content-prefetch targets for one account (CAN raise).

    Thin translation wrapper over
    ``email_metadata_store.list_unread_recent_uncached``. The prefetch
    caller wraps this in its own best-effort try/except, so this follows
    the standard translation pattern rather than swallowing.
    """
    try:
        return email_metadata_store.list_unread_recent_uncached(account_id, limit)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected unread recent uncached listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback("Failed to list unread recent uncached messages.") from exc


_MAX_SEARCH_TOKENS = 10


def parse_search_tokens(q: str | None) -> list[str]:
    """Strip *q* and split into up to 10 non-empty whitespace-separated tokens."""
    if q is None:
        return []
    return [t for t in q.strip().split() if t][:_MAX_SEARCH_TOKENS]


# ---------------------------------------------------------------------------
# Search query parser (free text + Gmail-style operators)
# ---------------------------------------------------------------------------
#
# The lupa (``GET /emails`` and the virtual-mailbox listing) accepts a single
# ``q`` string that mixes free-text tokens with Gmail-style operators
# (``from:`` ``to:`` ``subject:`` ``has:attachment`` ``before:`` ``after:``
# ``is:read|unread|favorite|starred`` ``in:``). ``parse_search_query`` splits
# ``q`` into three independent buckets so the service can pass free-text
# tokens (unchanged semantics) and typed operator clauses to the repository,
# and apply the ``in:`` box override. Everything stays a pure string
# operation — no DB, no provider call.
#
# Tolerance policy (decided with the user, see the feature docs):
#   - Unknown operator key (``foo:bar``)        -> treated as a literal free
#                                                  text token (handled by the
#                                                  tokenizer: the prefix is not
#                                                  a known operator so the whole
#                                                  term, colon included, is a
#                                                  value).
#   - Known operator with unsupported value     -> the operator is dropped
#     (``is:importante``, ``has:drive``,           (handled in the translation
#      ``in:archivados``, ``before:ayer``)         step below).
# Never raises on the content of ``q``; only the router's ``min_length`` /
# ``max_length`` can reject ``q``.

_MAX_FREE_TOKENS = _MAX_SEARCH_TOKENS  # free-text tokens cap (same as the lupa)
_MAX_OPERATOR_CLAUSES = 10             # operator-clause cap; the excess is dropped

_KNOWN_OPERATORS = {"from", "to", "subject", "has", "before", "after", "is", "in"}

# ``is:`` values map to the (clause_kind, typed_value) the repository expects.
_IS_VALUES: dict[str, tuple[str, bool]] = {
    "read": ("is_read_op", True),
    "unread": ("is_read_op", False),
    "favorite": ("is_favorite_op", True),
    "starred": ("is_favorite_op", True),
}
_HAS_VALUES = {"attachment", "attachments"}
# ``in:`` maps to a stored ``box`` value. ``DELETED`` is intentionally absent
# — it is an internal "trash emptied" state the lupa must not be able to
# select (``in:trash`` targets ``TRASH``, never ``DELETED``).
_IN_VALUES: dict[str, str] = {
    "inbox": "ALL_MAIL",
    "allmail": "ALL_MAIL",
    "sent": "SENT",
    "spam": "SPAM",
    "trash": "TRASH",
    "archive": "ARCHIVE",
}

_SEARCH_TIMEZONE = ZoneInfo("Europe/Madrid")
# Accept AAAA/MM/DD and AAAA-MM-DD only; zero-padded, fixed widths. ``before:``
# / ``after:`` are interpreted at local Madrid midnight (see _parse_date_boundary).
_DATE_RE = re.compile(r"^(\d{4})[/-](\d{2})[/-](\d{2})$")

# Free-text/operator prefix is ASCII letters only; anything else (digit,
# accent, punctuation) starting a term means the term is not an operator.
_KEYWORD_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class ParsedSearchQuery:
    """The decomposition of a raw ``q`` search string.

    - ``tokens``: free-text phrases (quotes already stripped), capped at
      ``_MAX_FREE_TOKENS``. Same semantics the lupa always had (substring,
      accent/case-insensitive, OR across subject/from, AND across tokens).
    - ``operator_clauses``: ``(kind, typed_value)`` pairs the repository
      resolves against ``_OPERATOR_CLAUSE_BUILDERS``, capped at
      ``_MAX_OPERATOR_CLAUSES``. ``kind`` carries the ``_op`` suffix (or a
      distinct name) on purpose so it never collides with the saved-filter
      keys of ``_EXTRA_FILTER_BUILDERS``.
    - ``box_override``: a stored box value from ``in:`` (``ALL_MAIL`` / ``SENT``
      / ``SPAM`` / ``TRASH``) or ``None``. The service decides how to apply it
      (override in the regular listing, intersection in a virtual mailbox).
    """

    tokens: list[str] = field(default_factory=list)
    operator_clauses: list[tuple[str, Any]] = field(default_factory=list)
    box_override: str | None = None


def _parse_date_boundary(value: str) -> datetime | None:
    """Parse ``AAAA/MM/DD`` / ``AAAA-MM-DD`` into local Madrid midnight.

    Returns ``None`` for any malformed or out-of-range date (e.g. month 13,
    day 32, non-zero-padded fields) so the caller can silently drop the
    filter. The tz is the real ``Europe/Madrid`` zone (CET/CEST), not a fixed
    offset — ``datetime(..., tzinfo=ZoneInfo(...))`` yields the correct local
    midnight across DST so the ``>=`` / ``<`` boundary comparisons against
    ``received_at`` (TIMESTAMPTZ) line up with what the user means.
    """
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(year, month, day, tzinfo=_SEARCH_TIMEZONE)
    except ValueError:
        return None


def _tokenize_search_query(q: str) -> list[tuple[str | None, str]]:
    """Left-to-right scan of *q* into ``(keyword|None, value)`` terms.

    A term is an operator only when an ASCII-letter prefix is immediately
    followed by ``:`` AND the lowercased prefix is a known operator; the
    ``keyword`` is then the lowercased prefix and ``value`` is read next.
    Anything else is free text: ``keyword`` is ``None`` and the whole term
    (letters and colon included) becomes the ``value`` — this is what turns
    an unknown operator like ``foo:bar`` into a literal token.

    Value reading honours double quotes: a ``"`` opens a quoted value read up
    to the next ``"`` (quotes excluded, the content may contain spaces and
    ``:``); an unterminated quote takes the rest of the string. An unquoted
    value runs up to the next whitespace.
    """
    terms: list[tuple[str | None, str]] = []
    i = 0
    n = len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue

        keyword: str | None = None
        # Try to read an operator keyword: ASCII letters + immediate ':'.
        km = _KEYWORD_RE.match(q, i)
        if km is not None:
            end = km.end()
            if end < n and q[end] == ":" and km.group(0).lower() in _KNOWN_OPERATORS:
                keyword = km.group(0).lower()
                i = end + 1  # consume the keyword and the ':'

        # Read the value (quoted or up to whitespace). When this term is free
        # text (keyword is None) the scan starts at the term's first char, so
        # the letters/colon are part of the value.
        if i < n and q[i] == '"':
            i += 1
            start = i
            while i < n and q[i] != '"':
                i += 1
            value = q[start:i]
            if i < n:  # skip the closing quote when present
                i += 1
        else:
            start = i
            while i < n and not q[i].isspace():
                i += 1
            value = q[start:i]

        terms.append((keyword, value))
    return terms


def parse_search_query(q: str | None) -> ParsedSearchQuery:
    """Decompose *q* into free-text tokens, operator clauses and a box override.

    See :class:`ParsedSearchQuery` and the module-level notes for the
    tolerance policy. Never raises on the content of *q*.
    """
    if q is None:
        return ParsedSearchQuery()
    q = q.strip()
    if not q:
        return ParsedSearchQuery()

    tokens: list[str] = []
    operator_clauses: list[tuple[str, Any]] = []
    box_override: str | None = None

    for keyword, value in _tokenize_search_query(q):
        if keyword is None:
            if value:
                tokens.append(value)
            continue

        if keyword in ("from", "to", "subject"):
            if value:
                kind = {
                    "from": "from_contains",
                    "to": "to_contains",
                    "subject": "subject_contains_op",
                }[keyword]
                operator_clauses.append((kind, value))
            continue

        if keyword == "has":
            if value.lower() in _HAS_VALUES:
                operator_clauses.append(("has_attachments", True))
            continue

        if keyword in ("before", "after"):
            boundary = _parse_date_boundary(value)
            if boundary is not None:
                kind = "received_before" if keyword == "before" else "received_after"
                operator_clauses.append((kind, boundary))
            continue

        if keyword == "is":
            mapped = _IS_VALUES.get(value.lower())
            if mapped is not None:
                operator_clauses.append(mapped)
            continue

        if keyword == "in":
            mapped_box = _IN_VALUES.get(value.lower())
            if mapped_box is not None:
                # The last valid ``in:`` wins when several are present.
                box_override = mapped_box
            continue

    return ParsedSearchQuery(
        tokens=tokens[:_MAX_FREE_TOKENS],
        operator_clauses=operator_clauses[:_MAX_OPERATOR_CLAUSES],
        box_override=box_override,
    )


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------


def recompute_has_attachments(
    account_id: str,
    provider_message_id: str,
    *,
    fallback: type[ApiError] = ApiError,
) -> None:
    """Recalculate and persist ``email_metadata.has_attachments`` (D-09).

    The single call site is the cache-miss branch of
    ``get_email_content`` — after upserting the new ``email_attachments``
    rows, this helper rewrites the denormalised flag so the inbox
    listing reflects the discovery without an extra round trip. The
    underlying SQL is idempotent (it derives the flag from the live
    count of non-inline rows) so calling twice in a row is harmless.
    """
    try:
        email_metadata_store.update_has_attachments(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected has_attachments recompute error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback(
            "Failed to recompute has_attachments after attachment upsert."
        ) from exc


def row_to_email_metadata_out(row: dict[str, Any]) -> EmailMetadataOut:
    """Map an ``email_metadata`` row dict into the API response model.

    Centralised so the regular box listing AND the virtual-mailbox
    listing always project the same fields (including ``is_favorite``
    and ``has_attachments``).
    """
    return EmailMetadataOut(
        provider_message_id=row["provider_message_id"],
        account_id=str(row["account_id"]),
        mailbox_id=str(row["mailbox_id"]),
        thread_id=row.get("thread_id"),
        from_email=row["from_email"],
        from_name=row.get("from_name"),
        to_email=row.get("to_email") or None,
        to_name=row.get("to_name") or None,
        subject=row.get("subject"),
        received_at=row["received_at"],
        is_read=row["is_read"],
        box=row["box"],
        has_attachments=bool(row.get("has_attachments", False)),
        is_favorite=bool(row.get("is_favorite", False)),
        thread_message_count=int(row.get("thread_message_count", 1) or 1),
    )
