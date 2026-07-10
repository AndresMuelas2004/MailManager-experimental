"""Contexto de cuentas: acceso a mailbox, construccion del manager, credenciales, tokens y errores silenciosos de auth."""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from pydantic import SecretStr

from api.errors.exceptions import (
    AccountMisconfigured,
    AccountNotConnected,
    AccountTokensLoadError,
    ApiError,
    AppCredentialsLoadError,
    Forbidden,
    MailboxLookupError,
    MailboxNotFound,
)
from api.schemas.email import AccountSyncFailure
from core.email import CoreError, EmailManager
from database import (
    account_store,
    load_app_credentials,
    mailbox_store,
    DatabaseError,
)

from .traduccion_errores import (
    is_auth_error,
    translate_core_error,
    translate_database_error,
)


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
        # The ONLY place this failure's real cause becomes observable: a
        # partial-success sync responds 200 (never reaches the ApiError
        # handler) and the wire reason is a bounded category (no-leak).
        # Log the full cause chain here or lose it.
        logger.log(
            logging.WARNING if reason == "account_not_connected" else logging.ERROR,
            "Sync failure for account %s (%s) reported as '%s' in failed_accounts.",
            account_id, provider, reason,
            exc_info=error,
        )
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
