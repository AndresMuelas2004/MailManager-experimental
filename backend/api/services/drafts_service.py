"""
Service layer for draft operations.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import UploadFile

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    AttachmentBlockedExtension,
    AttachmentInsertError,
    AttachmentLimitExceeded,
    AttachmentListingError,
    AttachmentLookupError,
    AttachmentMessageSizeExceeded,
    AttachmentSendFailed,
    AttachmentTooLarge,
    DraftAttachmentNotFound,
    DraftCreationError,
    DraftDeleteError,
    DraftListError,
    DraftNotFound,
    DraftSendError,
    DraftSyncError,
    DraftUpdateError,
)
from api.schemas.attachment import (
    DraftAttachmentMetadataOut,
    DraftAttachmentResponseOut,
)
from api.schemas.draft import (
    DraftCreate,
    DraftOut,
    DraftSendOut,
    DraftsAccountSyncDetail,
    DraftsSyncResultOut,
    DraftUpdate,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    persist_email_metadata_batch,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
    unwrap_secret,
)
from core.email import (
    CoreError,
    DraftAttachmentInput,
    EmailAttachmentSendFailed,
    is_blocked_extension,
)
from core.email.helpers import sanitize_filename
from database import (
    account_store,
    draft_attachment_store,
    draft_store,
    DatabaseError,
)


# Limits enforced server-side as the second line of defence (D-01/02/03).
# The frontend already validates before upload, but the spec keeps this
# validation here so the API stays correct even if the client diverges.
_MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
_MAX_MESSAGE_SIZE_BYTES = 25 * 1024 * 1024
_MAX_ATTACHMENTS_PER_MESSAGE = 25


def _load_draft_attachments_for_out(
    account_id: str, provider_draft_id: str,
) -> list[DraftAttachmentMetadataOut]:
    """Load draft attachments metadata + map to the DraftOut schema.

    Used by every site that builds a :py:class:`DraftOut` so the
    attachments list survives create / update / list / sync — the
    composer relies on it to repaint chips when it reopens an
    existing draft. Errors are translated through the shared
    database/Api fallback path so callers stay simple.
    """
    try:
        rows = draft_attachment_store.list_by_draft(account_id, provider_draft_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft attachments load error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftListError(
            "Failed to load draft attachments for draft view."
        ) from exc
    return [
        DraftAttachmentMetadataOut(
            draft_attachment_id=str(row["draft_attachment_id"]),
            filename=str(row.get("filename") or "attachment"),
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            size=int(row.get("size") or 0),
            position=int(row.get("position") or 0),
            provider_attachment_id=row.get("provider_attachment_id"),
        )
        for row in rows
    ]


def _draft_out_from_row(row: dict[str, Any]) -> DraftOut:
    """Build a :py:class:`DraftOut` from a persisted row + attachments.

    Centralises the conversion so every endpoint that returns a draft
    surfaces the same shape — including the composer-critical
    ``attachments`` field.

    Two row shapes are accepted:

    * **Single-draft endpoints** (create / update / get / send) — the row
      has no ``attachments`` key, so we fetch them in a follow-up query
      (one extra round trip for one draft).
    * **List endpoints** — the row already carries an ``attachments``
      list, pre-aggregated by the listing SQL via ``json_agg`` to avoid
      the 1 + N round trips a per-row fetch would cost (Phase 2.5).
    """
    account_id = str(row["account_id"])
    provider_draft_id = str(row["provider_draft_id"])
    if "attachments" in row:
        attachments = [
            DraftAttachmentMetadataOut(
                draft_attachment_id=str(item["draft_attachment_id"]),
                filename=str(item.get("filename") or "attachment"),
                mime_type=str(item.get("mime_type") or "application/octet-stream"),
                size=int(item.get("size") or 0),
                position=int(item.get("position") or 0),
                provider_attachment_id=item.get("provider_attachment_id"),
            )
            for item in (row.get("attachments") or [])
        ]
    else:
        attachments = _load_draft_attachments_for_out(account_id, provider_draft_id)
    return DraftOut(
        provider_draft_id=provider_draft_id,
        account_id=account_id,
        to_recipients=row.get("to_recipients") or [],
        cc_recipients=row.get("cc_recipients") or [],
        bcc_recipients=row.get("bcc_recipients") or [],
        subject=row.get("subject") or "",
        body=row.get("body") or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        attachments=attachments,
    )


def _persist_refreshed_tokens(
    updated_tokens: dict[str, dict[str, Any]],
    label_lookup: dict[str, tuple[str, str, str]],
    *,
    fallback: type[ApiError] = DraftCreationError,
) -> None:
    """Persist any refreshed tokens after a silent auth call during draft operations."""
    for account_label, token_payload in updated_tokens.items():
        ids = label_lookup.get(account_label)
        if not ids:
            continue
        mailbox_id, account_id, provider = ids
        payload = dict(token_payload or {})
        payload["access_token"] = unwrap_secret(payload.get("access_token"))
        payload["refresh_token"] = unwrap_secret(payload.get("refresh_token"))
        try:
            account_store.upsert_tokens(mailbox_id, account_id, provider, payload)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft token refresh persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise fallback(
                "Failed to persist refreshed tokens during draft operation."
            ) from exc


def create_draft(
    mailbox_id: str,
    account_id: str,
    payload: DraftCreate,
    user_id: str,
) -> DraftOut:
    """
    Create a draft at the provider and persist it locally.
    Provider-First: only persist if the provider call succeeds.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft creation (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftCreationError(
            "Failed to look up account while creating draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft creation."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftCreationError,
        )

        try:
            draft_metadata = manager.create_draft(
                account_label,
                payload.to_recipients,
                payload.cc_recipients,
                payload.bcc_recipients,
                payload.subject,
                payload.body,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftCreationError,
                context={"account_id": account_id, "account_label": account_label},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft creation (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftCreationError(
                "Unexpected failure while creating draft at provider."
            ) from exc

        row = {
            "provider_draft_id": draft_metadata.provider_draft_id,
            "account_id": account_id,
            "to_recipients": list(payload.to_recipients),
            "cc_recipients": list(payload.cc_recipients),
            "bcc_recipients": list(payload.bcc_recipients),
            "subject": payload.subject,
            "body": payload.body,
        }
        try:
            persisted = draft_store.create(row)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftCreationError(
                "Failed to persist draft to database after provider creation."
            ) from exc

        return _draft_out_from_row(persisted)
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft creation error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftCreationError("Failed to create draft.") from exc


def update_draft(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    payload: DraftUpdate,
    user_id: str,
) -> DraftOut:
    """
    Replace an existing draft at the provider and persist the new
    content locally. Provider-First: the provider call runs before any
    DB write, so the local row is only updated after the provider
    acknowledges the change.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft update (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError(
            "Failed to look up account while updating draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft update."
        )

    try:
        existing_draft = draft_store.get(provider_draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during draft update (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError(
            "Failed to look up draft while updating it."
        ) from exc
    if existing_draft is None:
        raise DraftNotFound(
            f"Draft '{provider_draft_id}' not found for account "
            f"'{account_id}' during draft update."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(
                updated_tokens, label_lookup, fallback=DraftUpdateError,
            )
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftUpdateError,
        )

        try:
            manager.update_draft(
                account_label,
                provider_draft_id,
                payload.to_recipients,
                payload.cc_recipients,
                payload.bcc_recipients,
                payload.subject,
                payload.body,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftUpdateError,
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "provider_draft_id": provider_draft_id,
                },
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft update (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftUpdateError(
                "Unexpected failure while updating draft at provider."
            ) from exc

        row = {
            "provider_draft_id": provider_draft_id,
            "account_id": account_id,
            "to_recipients": list(payload.to_recipients),
            "cc_recipients": list(payload.cc_recipients),
            "bcc_recipients": list(payload.bcc_recipients),
            "subject": payload.subject,
            "body": payload.body,
        }
        try:
            persisted = draft_store.update(row)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB persist error during update (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftUpdateError(
                "Failed to persist draft to database after provider update."
            ) from exc

        return _draft_out_from_row(persisted)
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError("Failed to update draft.") from exc


def delete_draft(
    mailbox_id: str,
    account_id: str,
    draft_id: str,
    user_id: str,
) -> dict[str, str]:
    """
    Delete a draft at the provider and then from the local database.
    Provider-First: only delete locally if the provider call succeeds.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft deletion (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError(
            "Failed to look up account while deleting draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft deletion."
        )

    try:
        existing = draft_store.get(draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during draft deletion (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError(
            "Failed to look up draft for deletion."
        ) from exc
    if existing is None:
        raise DraftNotFound(
            f"Draft '{draft_id}' not found for account '{account_id}' "
            "during draft deletion."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=DraftDeleteError)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftDeleteError,
        )

        try:
            manager.delete_draft(account_label, draft_id)
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftDeleteError,
                context={"account_id": account_id, "draft_id": draft_id},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft deletion (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftDeleteError(
                "Unexpected failure while deleting draft at provider."
            ) from exc

        try:
            draft_store.delete(draft_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB delete error (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftDeleteError(
                "Failed to delete draft from database after provider deletion."
            ) from exc

        return {"status": "deleted"}
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft deletion error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError("Failed to delete draft.") from exc


def list_drafts(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
) -> list[DraftOut]:
    """
    List drafts for a mailbox, optionally filtered to a single account.

    Pure DB read: does not contact any provider. Enforces mailbox ownership
    via ensure_mailbox_access.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during draft listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftListError(
                "Failed to look up account for draft listing."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during draft listing."
            )

        try:
            rows = draft_store.list_by_account(account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft listing error for account '%s' (%s): %s",
                account_id, type(exc).__name__, exc,
            )
            raise DraftListError(
                "Unexpected failure while listing drafts by account."
            ) from exc
    else:
        try:
            rows = draft_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft listing error for mailbox '%s' (%s): %s",
                mailbox_id, type(exc).__name__, exc,
            )
            raise DraftListError(
                "Unexpected failure while listing drafts across mailbox."
            ) from exc

    return [_draft_out_from_row(row) for row in rows]


def _build_draft_auth_context(
    accounts: list[dict[str, Any]],
    mailbox_id: str,
) -> tuple[
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    dict[str, tuple[str, str, str]],
]:
    """Build (auth_payloads, label_lookup) for a batch of accounts.

    Generalizes the inline auth-context setup used by ``create_draft``
    so ``sync_drafts`` can apply it uniformly to both the single-account
    and the unified-mailbox branches.
    """
    auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    label_lookup: dict[str, tuple[str, str, str]] = {}
    for account in accounts:
        account_id_local = str(account.get("account_id") or "")
        provider = str(account.get("provider") or "").lower()
        label = f"{mailbox_id}__{account_id_local}"
        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id_local, provider)
        auth_payloads[label] = (app_credentials, user_tokens)
        label_lookup[label] = (mailbox_id, account_id_local, provider)
    return auth_payloads, label_lookup


def sync_drafts(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
) -> DraftsSyncResultOut:
    """
    Load drafts from the provider(s) into the local drafts table.

    If account_id is None, syncs every account in the mailbox; otherwise
    only that specific account. Ownership enforced via ensure_mailbox_access.
    Per account, the full provider draft list replaces the local rows
    atomically (upsert + delete-missing). Both providers cap the fetch
    at _DRAFTS_MAX_TOTAL = 100 drafts per account.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # Step 1: load accounts (single or full mailbox)
    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during draft sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftSyncError(
                "Failed to look up account for draft sync."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during draft sync."
            )
        accounts = [account]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account list error during draft sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftSyncError(
                "Failed to list accounts for draft sync."
            ) from exc

    if not accounts:
        return DraftsSyncResultOut(total_synced=0, accounts=[])

    # Step 2: build manager + silent auth
    try:
        auth_payloads, label_lookup = _build_draft_auth_context(accounts, mailbox_id)
        manager = build_manager_for_accounts(accounts)
        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=DraftSyncError)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftSyncError,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected error during draft sync setup (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError(
            "Failed to prepare draft sync context."
        ) from exc

    # Step 3: fetch drafts from all clients
    try:
        fetch_results = manager.fetch_all_drafts()
    except CoreError as exc:
        raise translate_core_error(
            exc, fallback=DraftSyncError,
        ) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected fetch_all_drafts error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError(
            "Failed to fetch drafts from providers."
        ) from exc

    try:
        # Step 4: surface per-account errors captured by the manager
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftSyncError,
        )

        # Step 5: persist per account (atomic replace)
        account_details: list[DraftsAccountSyncDetail] = []
        total_synced = 0
        for label, drafts in fetch_results.items():
            ids = label_lookup.get(label)
            if not ids:
                continue
            _, account_id_inner, provider = ids
            rows = [
                {
                    "provider_draft_id": d.provider_draft_id,
                    "to_recipients": list(d.to_recipients),
                    "cc_recipients": list(d.cc_recipients),
                    "bcc_recipients": list(d.bcc_recipients),
                    "subject": d.subject,
                    "body": d.body,
                    "created_at": d.created_at,
                    "updated_at": d.updated_at,
                }
                for d in drafts
            ]
            try:
                synced_count = draft_store.replace_all_for_account(account_id_inner, rows)
            except DatabaseError as exc:
                raise translate_database_error(exc) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected persist error during draft sync for account '%s' (%s): %s",
                    account_id_inner, type(exc).__name__, exc,
                )
                raise DraftSyncError(
                    "Failed to persist drafts during sync."
                ) from exc

            total_synced += synced_count
            account_details.append(DraftsAccountSyncDetail(
                account_id=account_id_inner,
                provider=provider,
                drafts_synced=synced_count,
            ))

        return DraftsSyncResultOut(
            total_synced=total_synced,
            accounts=account_details,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft sync error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError("Unexpected failure during draft sync.") from exc


def send_draft(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    user_id: str,
) -> DraftSendOut:
    """
    Send an existing draft via the provider and clean up the local row.
    Provider-First: the provider send runs before any DB changes.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # 1. Account lookup
    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft send (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSendError(
            "Failed to look up account while sending draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft send."
        )

    # 2. Pre-check: draft exists locally
    try:
        existing_draft = draft_store.get(provider_draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during draft send (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSendError(
            "Failed to look up draft while sending it."
        ) from exc
    if existing_draft is None:
        raise DraftNotFound(
            f"Draft '{provider_draft_id}' not found for account "
            f"'{account_id}' during draft send."
        )

    # 3. Build manager, silent auth, refresh tokens
    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(
            mailbox_id, account_id, provider,
        )
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(
                updated_tokens, label_lookup, fallback=DraftSendError,
            )
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftSendError,
        )

        # 4. Recolectar adjuntos locales del draft (D-07 lazy push). For
        # each attachment NOT yet uploaded to the provider we need the
        # binary; already-uploaded attachments (Outlook, partial-success
        # retry) only need their provider_attachment_id (D-27).
        draft_attachment_inputs = _collect_draft_attachment_inputs(
            account_id, provider_draft_id,
        )

        # 5. Provider-First: send with attachments (atomic for Gmail,
        # multi-step for Outlook). Partial successes during the Outlook
        # path are persisted *before* re-raising the failure so a retry
        # can skip the already-uploaded parts.
        try:
            sent_metadata, upload_results = manager.send_draft_with_attachments(
                account_label,
                provider_draft_id,
                existing_draft.get("to_recipients") or [],
                existing_draft.get("cc_recipients") or [],
                existing_draft.get("bcc_recipients") or [],
                str(existing_draft.get("subject") or ""),
                str(existing_draft.get("body") or ""),
                draft_attachment_inputs,
            )
        except EmailAttachmentSendFailed as exc:
            _persist_partial_upload_results(exc.detail or {})
            raise translate_core_error(
                exc,
                fallback=AttachmentSendFailed,
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "provider_draft_id": provider_draft_id,
                },
            ) from exc
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftSendError,
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "provider_draft_id": provider_draft_id,
                },
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft send (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftSendError(
                "Unexpected failure while sending draft at provider."
            ) from exc

        # On success, persist any provider_attachment_ids that came
        # back from Outlook so a future delete-draft call can clean up
        # any orphans cleanly. CASCADE on the draft delete eventually
        # wipes draft_attachments anyway, so this is best-effort. Single
        # batch call avoids the per-attachment N+1 (D-03 caps at 25 rows
        # but the round-trip cost still adds up under load).
        success_pairs = [
            (result.draft_attachment_id, result.provider_attachment_id)
            for result in upload_results
            if result.provider_attachment_id
        ]
        if success_pairs:
            try:
                draft_attachment_store.batch_update_provider_attachment_ids(
                    success_pairs,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist provider_attachment_ids batch (%d rows, %s): %s",
                    len(success_pairs), type(exc).__name__, exc,
                )

        # 5. Best-effort: delete from drafts table
        try:
            draft_store.delete(provider_draft_id, account_id)
        except Exception as exc:
            logger.warning(
                "Draft sent but local draft row deletion failed for '%s' (%s): %s",
                provider_draft_id, type(exc).__name__, exc,
            )

        # 6. Best-effort: persist sent metadata to email_metadata
        sent_metadata.account_id = account_id
        try:
            persist_email_metadata_batch(account_id, [sent_metadata])
        except Exception as exc:
            logger.warning(
                "Draft sent but metadata persistence failed for account '%s' (%s): %s",
                account_id, type(exc).__name__, exc,
            )

        return DraftSendOut(
            provider_message_id=sent_metadata.provider_message_id,
            provider=provider,
            status="sent",
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft send error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSendError("Failed to send draft.") from exc


# ---------------------------------------------------------------------------
# Attachment-aware send helpers (D-07, D-27)
# ---------------------------------------------------------------------------


def _collect_draft_attachment_inputs(
    account_id: str, provider_draft_id: str,
) -> list[DraftAttachmentInput]:
    """Materialise ``DraftAttachmentInput`` objects for ``send_draft_with_attachments``.

    Loads each draft attachment WITH its blob (the metadata-only listing
    used by the composer is too thin for the send path). Already-uploaded
    attachments (``provider_attachment_id`` set) carry that id so the
    Outlook client can skip them in a partial-success retry.
    """
    try:
        rows = draft_attachment_store.list_by_draft_with_blob(
            account_id, provider_draft_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft attachments pre-send load error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSendError("Failed to load draft attachments before send.") from exc

    inputs: list[DraftAttachmentInput] = []
    for full_row in rows:
        local_id = str(full_row["draft_attachment_id"])
        blob = full_row.get("blob") or b""
        inputs.append(
            DraftAttachmentInput(
                draft_attachment_id=local_id,
                filename=str(full_row.get("filename") or "attachment"),
                mime_type=str(full_row.get("mime_type") or "application/octet-stream"),
                data=bytes(blob),
                size=int(full_row.get("size") or len(blob)),
                position=int(full_row.get("position") or 0),
                content_id=full_row.get("content_id"),
                is_inline=bool(full_row.get("is_inline", False)),
                provider_attachment_id=full_row.get("provider_attachment_id"),
            )
        )
    return inputs


def _persist_partial_upload_results(detail: dict[str, Any]) -> None:
    """Persist any ``succeeded`` upload results before re-raising the failure.

    Outlook's send is non-atomic: when one attachment fails the prior
    successes are already at the provider. The client returns them in
    ``detail['succeeded']`` so the service can stamp their
    ``provider_attachment_id`` locally — a retry then sees them as
    already-uploaded and skips the work (D-27 partial-success resume).
    """
    succeeded = detail.get("succeeded") if isinstance(detail, dict) else None
    if not succeeded:
        return
    pairs = [
        (entry.get("draft_attachment_id"), entry.get("provider_attachment_id"))
        for entry in succeeded
        if entry.get("draft_attachment_id") and entry.get("provider_attachment_id")
    ]
    if not pairs:
        return
    try:
        draft_attachment_store.batch_update_provider_attachment_ids(pairs)
    except Exception as exc:
        logger.warning(
            "Partial-success persist failed (%d pairs, %s): %s",
            len(pairs), type(exc).__name__, exc,
        )


# ---------------------------------------------------------------------------
# Add / remove draft attachment endpoints (D-07)
# ---------------------------------------------------------------------------


def add_draft_attachment(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    upload: UploadFile,
    user_id: str,
) -> DraftAttachmentResponseOut:
    """Persist a new attachment for an existing local draft (D-07).

    100% local: the provider is NOT contacted. The push to the provider
    happens during ``send_draft`` (Gmail) or ``send_draft_with_attachments``
    (Outlook). Validation runs server-side as the second line of defence
    even though the frontend already filters: D-04a (extension blocklist),
    D-01 (per-attachment 25 MB), D-02 (cumulative 25 MB), D-03 (max 25
    attachments per draft).
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during add_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up account while attaching to draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "while attaching to draft."
        )

    try:
        existing_draft = draft_store.get(provider_draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup during add_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up draft while attaching."
        ) from exc
    if existing_draft is None:
        raise DraftNotFound(
            f"Draft '{provider_draft_id}' not found for account '{account_id}' "
            "during add_draft_attachment."
        )

    raw_filename = (upload.filename or "attachment").strip()
    if is_blocked_extension(raw_filename):
        raise AttachmentBlockedExtension(
            f"Extension blocked for filename '{raw_filename}' on draft '{provider_draft_id}'."
        )

    data = upload.file.read()  # FastAPI buffers below 1 MB; >1 MB hits a SpooledTemporaryFile.
    if not isinstance(data, (bytes, bytearray)):
        raise AttachmentInsertError(
            "Multipart upload returned a non-bytes payload during add_draft_attachment."
        )
    size = len(data)
    if size > _MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentTooLarge(
            f"Single attachment '{raw_filename}' exceeds 25 MB limit on "
            f"draft '{provider_draft_id}'."
        )

    try:
        existing_attachments = draft_attachment_store.list_by_draft(
            account_id, provider_draft_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected list error during add_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentListingError(
            "Failed to list existing draft attachments before insert."
        ) from exc

    if len(existing_attachments) >= _MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentLimitExceeded(
            f"Draft '{provider_draft_id}' already holds {_MAX_ATTACHMENTS_PER_MESSAGE} "
            "attachments — cannot add another."
        )
    cumulative = sum(int(a.get("size") or 0) for a in existing_attachments) + size
    if cumulative > _MAX_MESSAGE_SIZE_BYTES:
        raise AttachmentMessageSizeExceeded(
            f"Adding '{raw_filename}' would push draft '{provider_draft_id}' over "
            "the 25 MB cumulative limit."
        )

    sanitised_name = sanitize_filename(
        raw_filename,
        existing=[str(a.get("filename") or "") for a in existing_attachments],
    )
    # ``position`` is computed inside the INSERT (atomic; see
    # queries/draft_attachments.py) so the service does not pre-resolve it.
    row = {
        "draft_attachment_id": str(uuid.uuid4()),
        "account_id": account_id,
        "provider_draft_id": provider_draft_id,
        "filename": sanitised_name,
        "mime_type": upload.content_type or "application/octet-stream",
        "size": size,
        "content_id": None,
        "is_inline": False,
        "blob": bytes(data),
    }
    try:
        persisted = draft_attachment_store.insert(row)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected insert error during add_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentInsertError(
            "Failed to persist draft attachment after validation."
        ) from exc

    return DraftAttachmentResponseOut(
        draft_attachment_id=str(persisted["draft_attachment_id"]),
        filename=str(persisted["filename"]),
        mime_type=str(persisted["mime_type"]),
        size=int(persisted["size"]),
        position=int(persisted["position"]),
        provider_attachment_id=persisted.get("provider_attachment_id"),
    )


def remove_draft_attachment(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    draft_attachment_id: str,
    user_id: str,
) -> dict[str, str]:
    """Remove an attachment row from a draft (D-07). Local-only operation."""
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during remove_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up account while removing draft attachment."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "while removing draft attachment."
        )

    try:
        row = draft_attachment_store.get(draft_attachment_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft attachment lookup during remove_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up draft attachment during removal."
        ) from exc
    if row is None:
        raise DraftAttachmentNotFound(
            f"Draft attachment '{draft_attachment_id}' not found during removal."
        )
    if (
        str(row.get("provider_draft_id") or "") != provider_draft_id
        or str(row.get("account_id") or "") != account_id
    ):
        raise DraftAttachmentNotFound(
            f"Draft attachment '{draft_attachment_id}' does not belong to draft "
            f"'{provider_draft_id}' in account '{account_id}'."
        )

    try:
        deleted = draft_attachment_store.delete(draft_attachment_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected delete error during remove_draft_attachment (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError(
            "Failed to delete draft attachment after ownership check."
        ) from exc
    if not deleted:
        # Concurrent delete from another tab — treat as success.
        logger.info(
            "Draft attachment %s already removed by a concurrent request.",
            draft_attachment_id,
        )
    return {"status": "deleted"}
