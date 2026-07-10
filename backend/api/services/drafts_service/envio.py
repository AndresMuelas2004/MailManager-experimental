"""Envio de borradores con adjuntos (D-07/D-27) y sus helpers de recoleccion/persistencia parcial."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    AttachmentSendFailed,
    DraftNotFound,
    DraftSendError,
)
from api.schemas.draft import DraftSendOut
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    persist_email_metadata_batch,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
)
from core.email import (
    CoreError,
    DraftAttachmentInput,
    EmailAttachmentSendFailed,
)
from database import (
    account_store,
    draft_attachment_store,
    draft_store,
    DatabaseError,
)

from ._comunes import _persist_refreshed_tokens


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
        #
        # Reply / forward metadata (Gmail-only on the wire — Outlook
        # threading is already fixed by createReply/All/Forward at
        # creation): the values come from the local draft row, NOT
        # from the request body. ``buildDraftPayload`` in the frontend
        # deliberately omits them so the row stays the single source
        # of truth — see repository_guide.md "reply fields persisted
        # in row" invariant.
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
                in_reply_to=existing_draft.get("in_reply_to"),
                references=existing_draft.get("references_header"),
                thread_id=existing_draft.get("thread_id"),
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
                    exc_info=exc,
                )

        # 5. Best-effort: delete from drafts table
        try:
            draft_store.delete(provider_draft_id, account_id)
        except Exception as exc:
            logger.warning(
                "Draft sent but local draft row deletion failed for '%s' (%s): %s",
                provider_draft_id, type(exc).__name__, exc,
                exc_info=exc,
            )

        # 6. Best-effort: persist sent metadata to email_metadata
        sent_metadata.account_id = account_id
        try:
            persist_email_metadata_batch(account_id, [sent_metadata])
        except Exception as exc:
            logger.warning(
                "Draft sent but metadata persistence failed for account '%s' (%s): %s",
                account_id, type(exc).__name__, exc,
                exc_info=exc,
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
