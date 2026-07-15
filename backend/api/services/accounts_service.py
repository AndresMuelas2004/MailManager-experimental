"""
Service layer for account operations.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountConnectAuthError,
    AccountLimitExceeded,
    AccountNotFound,
    AccountOperationError,
    ApiError,
)
from core.email import CoreError
from api.schemas.account import (
    AccountConnectStartResponse,
    AccountCreate,
    AccountOut,
    AccountQuotaOut,
    AccountUpdate,
)
from api.services import oauth_pending
from api.services.backfill_service import (
    enqueue_backfill_on_connect,
    enqueue_draft_sync_on_connect,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_app_credentials,
    sanitize_outbound_html,
    translate_connect_error,
    translate_database_error,
    unwrap_secret,
)
from database import (
    DatabaseError,
    account_store,
    get_frontend_origin,
    get_google_oauth_redirect_uri,
)


_DEFAULT_MAX_ACCOUNTS_PER_USER = 15


def _max_accounts_per_user() -> int:
    """Per-user connected-account limit (env ``MAX_ACCOUNTS_PER_USER``, default
    15). Single source of truth for both the ``create_account`` guard and
    ``GET /accounts/quota`` — read at the point of use (same pattern as the
    backfill config) so tests can toggle it with ``monkeypatch.setenv``."""
    raw = os.environ.get("MAX_ACCOUNTS_PER_USER")
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_ACCOUNTS_PER_USER
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid MAX_ACCOUNTS_PER_USER=%r, defaulting to %d",
            raw, _DEFAULT_MAX_ACCOUNTS_PER_USER,
        )
        return _DEFAULT_MAX_ACCOUNTS_PER_USER


def _resolve_display_label(record: dict) -> str:
    display_label = record.get("display_label") or record.get("label")
    if display_label:
        return str(display_label)
    provider = record.get("provider", "account")
    account_id = record.get("account_id", "unknown")
    return f"{provider}:{account_id}"


def _build_response(record: dict) -> AccountOut:
    payload = dict(record)
    payload["display_label"] = _resolve_display_label(record)
    return AccountOut(**payload)


def list_accounts(mailbox_id: str, user_id: str) -> list[AccountOut]:
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected list accounts error (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to list accounts.") from exc
    return [_build_response(account) for account in accounts]


def create_account(mailbox_id: str, payload: AccountCreate, user_id: str) -> AccountOut:
    ensure_mailbox_access(mailbox_id, user_id)
    # Per-user account limit (decision 3A). ``create_account`` is the ONLY INSERT
    # into ``accounts`` (connect/callback only UPDATE tokens), so the guard here
    # is complete and race-free in practice. Counts ALL owned accounts, expired
    # tokens included.
    try:
        current = account_store.count_accounts_by_user(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account count error during creation (%s): %s",
            type(exc).__name__, exc,
        )
        raise AccountOperationError(
            "Failed to count user accounts before creating a new account."
        ) from exc
    limit = _max_accounts_per_user()
    if current >= limit:
        raise AccountLimitExceeded(
            f"User already owns the maximum of {limit} connected accounts; cannot create another.",
            {"limit": limit, "connected": current},
        )
    account_id = str(uuid4())
    record = {
        "account_id": account_id,
        "mailbox_id": mailbox_id,
        "provider": payload.provider,
        "display_label": payload.display_label,
        "config": payload.config,
    }
    try:
        created = account_store.upsert(record)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account creation error (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to create account.") from exc
    return _build_response(created)


def get_account_quota(user_id: str) -> AccountQuotaOut:
    """Return the user's connected-account usage vs the configured limit.

    User-scoped (no mailbox): counts every account the user owns across all
    their mailboxes. Reuses the SAME limit function as the create_account guard
    (single source of truth). Local-only (one indexed COUNT, no provider call).
    """
    try:
        connected = account_store.count_accounts_by_user(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account quota count error (%s): %s", type(exc).__name__, exc,
        )
        raise AccountOperationError("Failed to count user accounts for quota.") from exc
    return AccountQuotaOut(connected=connected, limit=_max_accounts_per_user())


def get_account(mailbox_id: str, account_id: str, user_id: str) -> AccountOut:
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        record = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to look up account for get.") from exc
    if record is None:
        raise AccountNotFound(f"Account '{account_id}' not found for get.")
    return _build_response(record)


def update_account(mailbox_id: str, account_id: str, payload: AccountUpdate, user_id: str) -> AccountOut:
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        record = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error during update (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to look up account for update.") from exc
    if record is None:
        raise AccountNotFound(f"Account '{account_id}' not found for update.")

    if payload.display_label is not None:
        record["display_label"] = payload.display_label
    if payload.config is not None:
        record["config"] = payload.config
    if payload.signature_html is not None:
        # Sanitise on persist (defence in depth): the signature is composed in
        # the same restricted rich-text editor as the body and is inserted into
        # the body at compose time, so it goes through the outbound allowlist
        # again on send. "" sanitises to "" (clears the signature).
        record["signature_html"] = sanitize_outbound_html(payload.signature_html)

    try:
        updated = account_store.upsert(record)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account update error (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to update account.") from exc
    return _build_response(updated)


def delete_account(mailbox_id: str, account_id: str, user_id: str) -> dict[str, str]:
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        record = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error during delete (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to look up account for delete.") from exc
    if record is None:
        raise AccountNotFound(f"Account '{account_id}' not found for delete.")
    # ON DELETE CASCADE removes associated tokens automatically.
    try:
        account_store.delete(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account deletion error (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to delete account.") from exc
    return {"status": "deleted"}


def start_account_connect(mailbox_id: str, account_id: str, user_id: str) -> AccountConnectStartResponse:
    """
    First half of the interactive connect flow: build the provider
    authorization URL and register the pending flow. The exchange happens in
    ``complete_account_connect`` when the OAuth callback arrives.
    """
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        record = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error during connect (%s): %s", type(exc).__name__, exc)
        raise AccountOperationError("Failed to look up account for connect.") from exc
    if record is None:
        raise AccountNotFound(f"Account '{account_id}' not found for connect.")

    provider = str(record.get("provider") or "").lower()
    account_label = f"{mailbox_id}__{account_id}"
    manager = build_manager_for_accounts([record])
    app_credentials = load_wrapped_app_credentials(provider)
    # Gmail needs the API-side callback as redirect; Outlook reads its
    # registered redirect from the credentials JSON (Azure must know it).
    redirect_uri = get_google_oauth_redirect_uri() if provider == "gmail" else None

    connect_context = {
        "account_id": account_id,
        "provider": record.get("provider"),
        "account_label": account_label,
    }
    try:
        begin = manager.begin_connect(account_label, app_credentials, redirect_uri=redirect_uri)
    except CoreError as exc:
        raise translate_connect_error(exc, context=connect_context) from exc
    except Exception as exc:
        logger.warning("Unexpected connect start error (%s): %s", type(exc).__name__, exc)
        raise AccountConnectAuthError("Failed to start the account connect flow.") from exc

    authorization_url = str(begin.get("authorization_url") or "")
    state = str(begin.get("state") or "")
    if not authorization_url or not state:
        raise AccountConnectAuthError("Connect flow did not produce an authorization URL.")

    oauth_pending.register(oauth_pending.PendingConnect(
        state=state,
        mailbox_id=mailbox_id,
        account_id=account_id,
        user_id=user_id,
        provider=provider,
        account_label=account_label,
        flow_state=dict(begin.get("flow_state") or {}),
    ))

    return AccountConnectStartResponse(
        provider=record.get("provider", ""),
        account_id=account_id,
        account_label=account_label,
        authorization_url=authorization_url,
        state=state,
    )


def complete_account_connect(
    state: str,
    code: str | None,
    error: str | None,
    error_description: str | None,
) -> dict[str, Any]:
    """
    Second half of the interactive connect flow, invoked by the OAuth
    redirect callback (no session: the single-use ``state`` token issued by
    an authenticated start is the proof of legitimacy).

    Never raises: the callback renders a human-facing HTML page, so every
    failure is reported as ``{"ok": False, ...}`` instead of the JSON error
    envelope. Returns ``{"ok", "provider", "message", "frontend_origin"}``.
    """
    result_base: dict[str, Any] = {"provider": None, "frontend_origin": get_frontend_origin()}
    pending = oauth_pending.pop(str(state or ""))
    if pending is None:
        return {
            **result_base,
            "ok": False,
            "message": "This connection attempt is unknown or expired. Close this tab and retry from the app.",
        }
    result_base["provider"] = pending.provider

    if error:
        # The provider error/description arrive as attacker-influenceable query
        # params on the unauthenticated callback. Never reflect them verbatim:
        # log the real values for diagnosis and surface only a fixed-vocabulary,
        # sanitised error code (defence in depth behind the router's script-
        # context escaping).
        logger.warning(
            "OAuth connect callback returned an error (error=%r, error_description=%r).",
            error,
            error_description,
        )
        safe_error = error if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error) else "unknown_error"
        return {
            **result_base,
            "ok": False,
            "message": f"The provider did not authorize the connection ({safe_error}).",
        }
    if not str(code or "").strip():
        return {**result_base, "ok": False, "message": "The authorization callback did not include a code."}

    try:
        record = account_store.get(pending.mailbox_id, pending.account_id)
    except Exception as exc:
        logger.warning("Account lookup failed completing connect (%s): %s", type(exc).__name__, exc)
        return {**result_base, "ok": False, "message": "Failed to look up the account while completing the connection."}
    if record is None:
        return {**result_base, "ok": False, "message": "The account no longer exists. Close this tab and retry from the app."}

    connect_context = {
        "account_id": pending.account_id,
        "provider": pending.provider,
        "account_label": pending.account_label,
    }
    try:
        manager = build_manager_for_accounts([record])
        app_credentials = load_wrapped_app_credentials(pending.provider)
        wrapped_tokens = manager.complete_connect(
            pending.account_label, app_credentials, pending.flow_state, code,
        )
    except ApiError as exc:
        logger.warning("Connect completion failed (%s): %s", type(exc).__name__, exc.message)
        return {**result_base, "ok": False, "message": exc.message}
    except CoreError as exc:
        translated = translate_connect_error(exc, context=connect_context)
        logger.warning("Connect completion failed (%s): %s", type(exc).__name__, translated.message)
        return {**result_base, "ok": False, "message": translated.message}
    except Exception as exc:
        logger.warning("Unexpected connect completion error (%s): %s", type(exc).__name__, exc)
        return {**result_base, "ok": False, "message": "Failed to complete the account connection."}

    token_payload = dict(wrapped_tokens or {})
    token_payload["access_token"] = unwrap_secret(token_payload.get("access_token"))
    token_payload["refresh_token"] = unwrap_secret(token_payload.get("refresh_token"))
    try:
        account_store.upsert_tokens(pending.mailbox_id, pending.account_id, pending.provider, token_payload)
    except Exception as exc:
        logger.warning("Token persist failed completing connect (%s): %s", type(exc).__name__, exc)
        return {**result_base, "ok": False, "message": "Failed to persist tokens after completing the connection."}

    # Enqueue the first-connection background backfill (best-effort, soft-fail):
    # a failure here must NOT flip the callback to ok:False — the tokens are
    # already persisted, and the user can retry the load by reconnecting (which
    # revives a job left 'failed'). Gated internally by BACKFILL_WORKER_ENABLED
    # and by sync_cursor (first connection only).
    try:
        enqueue_backfill_on_connect(pending.mailbox_id, pending.account_id, pending.provider)
    except Exception as exc:
        logger.warning(
            "Backfill enqueue on connect failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )

    # Enqueue the server-side draft sync (best-effort, soft-fail — same rule: a
    # failure here must NOT flip the callback to ok:False). Fires on every
    # connect, independent of the mail backfill, so the drafts refresh promptly.
    try:
        enqueue_draft_sync_on_connect(pending.mailbox_id, pending.account_id, pending.provider)
    except Exception as exc:
        logger.warning(
            "Draft sync enqueue on connect failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )

    return {**result_base, "ok": True, "message": "Account connected successfully. You can close this tab."}
