"""Helpers transversales de borradores: construccion de DraftOut, refresco de tokens y contexto de auth."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    ApiError,
    DraftCreationError,
    DraftListError,
)
from api.schemas.attachment import DraftAttachmentMetadataOut
from api.schemas.draft import DraftOut
from api.services.services_helpers import (
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    translate_database_error,
    unwrap_secret,
)
from database import (
    account_store,
    draft_attachment_store,
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
        # Reply / forward threading metadata. The GET / INSERT…RETURNING /
        # list SELECTs all project these six columns, so the row carries
        # them — surfacing them lets the composer repopulate the threading
        # context when a saved reply / forward draft is reopened.
        reply_kind=row.get("reply_kind"),
        reply_to_message_id=row.get("reply_to_message_id"),
        reply_to_account_id=row.get("reply_to_account_id"),
        thread_id=row.get("thread_id"),
        in_reply_to=row.get("in_reply_to"),
        references_header=row.get("references_header"),
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
