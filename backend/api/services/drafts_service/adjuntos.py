"""Adjuntos de borradores: alta, baja y copia desde un email recibido (D-07, R-06/R-12)."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    AttachmentBlockedExtension,
    AttachmentCopySourceUnavailable,
    AttachmentInsertError,
    AttachmentLimitExceeded,
    AttachmentListingError,
    AttachmentLookupError,
    AttachmentMessageSizeExceeded,
    AttachmentTooLarge,
    DraftAttachmentNotFound,
    DraftDeleteError,
    DraftNotFound,
    EmailNotFound,
)
from api.schemas.attachment import (
    CopyAttachmentsFromEmailResponse,
    DraftAttachmentMetadataOut,
    DraftAttachmentResponseOut,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    translate_database_error,
)
from core.email import (
    AttachmentMetadata,
    CoreError,
    EmailAttachmentNotFound,
    is_blocked_extension,
    sanitize_filename,
)
from database import (
    account_store,
    draft_attachment_store,
    draft_store,
    email_attachment_store,
    email_metadata_store,
    DatabaseError,
)

from ._comunes import (
    _MAX_ATTACHMENTS_PER_MESSAGE,
    _MAX_ATTACHMENT_SIZE_BYTES,
    _MAX_MESSAGE_SIZE_BYTES,
    _load_draft_attachments_for_out,
    _persist_refreshed_tokens,
)


# ---------------------------------------------------------------------------
# Add / remove draft attachment endpoints (D-07)
# ---------------------------------------------------------------------------


def add_draft_attachment(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    file_content: bytes,
    filename: str,
    content_type: str,
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

    raw_filename = (filename or "attachment").strip()
    if is_blocked_extension(raw_filename):
        raise AttachmentBlockedExtension(
            f"Extension blocked for filename '{raw_filename}' on draft '{provider_draft_id}'."
        )

    # The router has already read the multipart body into ``file_content``
    # (HTTP concerns stay in the router; the service speaks plain bytes).
    data = file_content
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
        "mime_type": content_type or "application/octet-stream",
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


# ---------------------------------------------------------------------------
# Copy attachments from a received email into a Forward draft (R-06 / R-12)
# ---------------------------------------------------------------------------


def _load_draft_attachments_metadata_out(
    account_id: str, provider_draft_id: str,
) -> list[DraftAttachmentMetadataOut]:
    """Re-export of the existing ``_load_draft_attachments_for_out`` helper.

    Kept as a thin alias so the copy endpoint can rebuild the chip list
    after persisting new rows; the existing helper already runs the
    DB → DraftAttachmentMetadataOut mapping with the right error
    handling.
    """
    return _load_draft_attachments_for_out(account_id, provider_draft_id)


def copy_attachments_from_email(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    source_account_id: str,
    source_provider_message_id: str,
    user_id: str,
) -> CopyAttachmentsFromEmailResponse:
    """Copy downloadable attachments from a received email into a Forward draft.

    R-06 / R-12: idempotent, partial-success-friendly. Gmail must
    download + re-upload each attachment via this endpoint because
    Gmail's API has no "attach by reference" primitive. Outlook
    inherits attachments automatically via ``createForward`` at draft
    creation, so this endpoint is a no-op for Outlook drafts and
    returns ``copied_count=0`` with the current attachment list — the
    frontend calls it uniformly for both providers.

    Flow:

    1. Mailbox + draft ownership pre-checks (D-22 anti-leak).
    2. Source account ownership via the single-JOIN repository method.
    3. If draft account is Outlook → no-op response.
    4. Read source attachments (``is_inline = FALSE``).
    5. Filter out already-copied rows via R-12 idempotency check.
    6. For each remaining attachment: reuse cached blob when present,
       otherwise download from provider; persist into ``draft_attachments``
       carrying ``source_account_id`` + ``source_attachment_id`` so a
       future retry skips the row.
    7. Skipped reasons surface as structured records (per-row
       failures do NOT abort the whole batch — best-effort).
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # Draft pre-check + account lookup (same pattern as add_draft_attachment).
    try:
        draft_account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up account while copying attachments from email."
        ) from exc
    if draft_account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during copy attachments from email."
        )

    try:
        existing_draft = draft_store.get(provider_draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to look up draft while copying attachments from email."
        ) from exc
    if existing_draft is None:
        raise DraftNotFound(
            f"Draft '{provider_draft_id}' not found for account '{account_id}' "
            "during copy attachments from email."
        )

    # Source account ownership — D-22 anti-leak via single JOIN. A
    # foreign or missing account uniformly collapses to 404.
    try:
        source_account = account_store.get_by_id_for_user(
            source_account_id, user_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected source account lookup error during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to verify source account while copying attachments from email."
        ) from exc
    if source_account is None:
        raise AccountNotFound(
            f"Source account '{source_account_id}' not found "
            "during copy attachments from email."
        )

    # Source email metadata existence — avoids spending a provider
    # round trip on a guaranteed 404.
    try:
        source_exists = email_metadata_store.exists(
            source_account_id, source_provider_message_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected source email existence check during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to verify source email existence during copy."
        ) from exc
    if not source_exists:
        raise EmailNotFound(
            f"Source email '{source_provider_message_id}' not found "
            f"for account '{source_account_id}' during copy attachments from email."
        )

    draft_provider = str(draft_account.get("provider") or "").lower()

    # Outlook: createForward already copied attachments server-side at
    # draft creation. Returning the current chip list keeps the
    # frontend single-path (it always invokes copy-from-email after
    # creating a Forward draft).
    if draft_provider == "outlook":
        return CopyAttachmentsFromEmailResponse(
            copied_count=0,
            skipped=[],
            attachments=_load_draft_attachments_metadata_out(
                account_id, provider_draft_id,
            ),
        )

    # Gmail path: download + re-upload.
    try:
        source_rows = email_attachment_store.list_by_message(
            source_account_id, source_provider_message_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected source attachments list error during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentListingError(
            "Failed to list source attachments during copy."
        ) from exc
    downloadable_sources = [row for row in source_rows if not row.get("is_inline")]

    # R-12 idempotency: skip rows already copied into this draft.
    try:
        already_copied = draft_attachment_store.list_existing_source_attachment_ids(
            account_id, provider_draft_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected existing source-id query during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentListingError(
            "Failed to read existing source ids during copy."
        ) from exc

    # Pre-load existing chip list to enforce D-03 (count) / D-02
    # (cumulative size) caps before downloading anything.
    try:
        existing_chips = draft_attachment_store.list_by_draft(
            account_id, provider_draft_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected existing chips query during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentListingError(
            "Failed to read existing chips during copy."
        ) from exc

    current_count = len(existing_chips)
    cumulative_size = sum(int(row.get("size") or 0) for row in existing_chips)

    # Build the source account label for provider calls. We need a
    # manager scoped to the SOURCE account because the attachments
    # live in that mailbox.
    source_mailbox_id = str(source_account.get("mailbox_id") or "")
    source_provider = str(source_account.get("provider") or "").lower()
    source_account_label = f"{source_mailbox_id}__{source_account_id}"

    try:
        source_manager = build_manager_for_accounts([source_account])
        source_app_credentials = load_wrapped_app_credentials(source_provider)
        source_user_tokens = load_wrapped_account_tokens(
            source_mailbox_id, source_account_id, source_provider,
        )
        source_auth_payloads = {
            source_account_label: (source_app_credentials, source_user_tokens),
        }
        source_label_lookup = {
            source_account_label: (
                source_mailbox_id, source_account_id, source_provider,
            ),
        }
        updated_tokens = source_manager.authenticate_all_silent(source_auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(
                updated_tokens, source_label_lookup, fallback=AttachmentLookupError,
            )
        raise_on_silent_auth_errors(
            source_manager.get_last_errors(), fallback=AttachmentLookupError,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected source manager build during copy_from_email (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentLookupError(
            "Failed to prepare source provider client during copy."
        ) from exc

    skipped: list[dict[str, str]] = []
    copied_count = 0
    for row in downloadable_sources:
        attachment_id = str(row["attachment_id"])
        filename = str(row.get("filename") or "attachment")
        size = int(row.get("size") or 0)

        if attachment_id in already_copied:
            skipped.append({"filename": filename, "reason": "already_copied"})
            continue
        if row.get("unavailable_at") is not None:
            skipped.append({"filename": filename, "reason": "unavailable_at_source"})
            continue
        if current_count + 1 > _MAX_ATTACHMENTS_PER_MESSAGE:
            skipped.append({"filename": filename, "reason": "attachment_limit_exceeded"})
            continue
        if cumulative_size + size > _MAX_MESSAGE_SIZE_BYTES:
            skipped.append({"filename": filename, "reason": "message_size_exceeded"})
            continue

        try:
            blob = email_attachment_store.get_blob(attachment_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected blob lookup during copy_from_email (%s): %s",
                type(exc).__name__, exc,
            )
            skipped.append({"filename": filename, "reason": "blob_lookup_failed"})
            continue

        if blob is None:
            # Cache miss — pull from the provider and persist for next time.
            meta = AttachmentMetadata(
                provider_message_id=source_provider_message_id,
                part_id=row.get("part_id"),
                provider_attachment_id=row.get("provider_attachment_id"),
                filename=filename,
                mime_type=str(row.get("mime_type") or "application/octet-stream"),
                size=size,
                content_id=row.get("content_id"),
                is_inline=False,
                position=int(row.get("position") or 0),
            )
            try:
                binary = source_manager.fetch_attachment_binary(
                    source_account_label, source_provider_message_id, meta,
                )
            except EmailAttachmentNotFound:
                try:
                    email_attachment_store.mark_unavailable(attachment_id)
                except Exception as inner_exc:
                    logger.warning(
                        "mark_unavailable failed during copy_from_email (%s): %s",
                        type(inner_exc).__name__, inner_exc,
                    )
                skipped.append({"filename": filename, "reason": "unavailable_at_source"})
                continue
            except CoreError as exc:
                logger.warning(
                    "Provider download failed during copy_from_email: %s", exc,
                )
                skipped.append({"filename": filename, "reason": "provider_unavailable"})
                continue
            except Exception as exc:
                logger.warning(
                    "Unexpected provider download during copy_from_email (%s): %s",
                    type(exc).__name__, exc,
                )
                skipped.append({"filename": filename, "reason": "provider_unavailable"})
                continue
            blob = binary.data
            try:
                email_attachment_store.insert_blob(attachment_id, blob)
            except Exception as exc:
                logger.warning(
                    "insert_blob failed during copy_from_email (%s): %s",
                    type(exc).__name__, exc,
                )

        # Persist into draft_attachments with the R-12 source-tracking
        # columns. ``draft_attachment_id`` is freshly minted — the row
        # is a copy, not a reference.
        try:
            draft_attachment_store.insert({
                "draft_attachment_id": str(uuid.uuid4()),
                "account_id": account_id,
                "provider_draft_id": provider_draft_id,
                "filename": filename,
                "mime_type": str(row.get("mime_type") or "application/octet-stream"),
                "size": size,
                "content_id": row.get("content_id"),
                "is_inline": False,
                "blob": blob,
                "source_account_id": source_account_id,
                "source_attachment_id": attachment_id,
            })
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft attachment insert during copy_from_email (%s): %s",
                type(exc).__name__, exc,
            )
            raise AttachmentInsertError(
                "Failed to persist copied draft attachment."
            ) from exc

        copied_count += 1
        current_count += 1
        cumulative_size += size

    return CopyAttachmentsFromEmailResponse(
        copied_count=copied_count,
        skipped=skipped,
        attachments=_load_draft_attachments_metadata_out(
            account_id, provider_draft_id,
        ),
    )
