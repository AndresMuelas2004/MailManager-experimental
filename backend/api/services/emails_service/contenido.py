"""Contenido de correos: fetch+persistencia del cuerpo, adjuntos descubiertos y sus proyecciones de salida."""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailContentFetchError,
    EmailNotFound,
)
from core.email import (
    CoreError,
    EmailManager,
    sanitize_filename,
)
from api.schemas.email import EmailContentOut
from api.schemas.attachment import AttachmentMetadataOut
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    get_email_content,
    persist_email_content,
    raise_on_silent_auth_errors,
    recompute_has_attachments,
    sanitize_email_html,
    touch_email_content_last_accessed,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    email_attachment_store,
    email_metadata_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


def _fetch_and_persist_email_content(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    provider_message_id: str,
) -> EmailContentOut:
    """One provider read → sanitise + persist body (+TTL) + attachments → out.

    Shared by ``get_email_full_content`` (cache miss) AND the sync-time
    content prefetch. A SINGLE ``fetch_content_with_attachments`` read
    returns the body, the downloadable attachment list and the inline
    ``cid_map`` together (D4 — one provider round trip instead of two).

    The body and attachment persistence are best-effort (logged, never
    aborting): the provider read already succeeded, so a DB hiccup must
    not turn a readable email into a 502. The provider read itself is the
    one hard failure point — it raises ``EmailContentFetchError`` (502),
    which the cache-miss caller surfaces and the prefetch caller swallows.

    The prefetch MUST go through this full path (body AND attachments):
    persisting only the body would make the next open a cache HIT that
    never re-discovers attachments (discovery happens only here), hiding
    the clip and the attachments forever on pre-cached mail.
    """
    try:
        content, metadata_list, _cid_map = manager.fetch_content_with_attachments(
            account_label, provider_message_id,
        )
    except CoreError as exc:
        raise translate_core_error(exc, fallback=EmailContentFetchError) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected error during provider fetch_content_with_attachments (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Unexpected provider failure while fetching email content with attachments."
        ) from exc

    sanitized_html = sanitize_email_html(content.html_body) if content.html_body else None

    # The upsert stamps ``last_accessed_at = now()`` for the new row, so a
    # fresh cache entry starts its 7-day TTL on persist (no separate touch).
    try:
        persist_email_content(
            account_id, provider_message_id, sanitized_html, content.text_body,
            fallback=EmailContentFetchError,
        )
    except Exception as exc:
        logger.warning(
            "Content fetched but DB persist failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )

    _persist_attachment_metadata(account_id, provider_message_id, metadata_list)
    try:
        recompute_has_attachments(
            account_id, provider_message_id, fallback=EmailContentFetchError,
        )
    except Exception as exc:
        logger.warning(
            "has_attachments recompute failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )

    attachments_out = _load_email_attachments_out(account_id, provider_message_id)
    return EmailContentOut(
        html_body=sanitized_html,
        text_body=content.text_body,
        attachments=attachments_out,
    )


def get_email_full_content(
    mailbox_id: str,
    provider_message_id: str,
    account_id: str,
    user_id: str,
) -> EmailContentOut:
    """Return full email body, fetching from provider on cache miss."""
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during content fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Failed to look up account for email content fetch."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during email content fetch."
        )

    # Defensive metadata existence check. Required since email_content now
    # has a composite FK to email_metadata; a missing metadata row would
    # otherwise surface as a 500 from the FK violation at upsert time.
    try:
        metadata_exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata existence check error during content fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Failed to verify email existence for content fetch."
        ) from exc
    if not metadata_exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            f"in mailbox '{mailbox_id}' during email content fetch."
        )

    row = get_email_content(account_id, provider_message_id, fallback=EmailContentFetchError)
    if row is not None:
        # Cache hit on the HTML body. Refresh the sliding TTL: an open
        # counts as an access, so frequently-read mail never expires
        # (best-effort — a touch failure must not break the read, and it
        # touches ONLY ``last_accessed_at``, not ``fetched_at``).
        touch_email_content_last_accessed(account_id, provider_message_id)
        # The email_attachments table is the source of truth for the
        # attachment list (D-13). Reading it always (not only on miss)
        # keeps the response consistent if a TTL purge later wiped the
        # blobs but kept the metadata rows.
        attachments_out = _load_email_attachments_out(account_id, provider_message_id)
        return EmailContentOut(
            html_body=row["html_body"],
            text_body=row["text_body"],
            attachments=attachments_out,
        )

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailContentFetchError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailContentFetchError)

        # Single provider read (D4): body + attachments + cid_map together.
        # The shared helper sanitises, persists the body (with a fresh TTL),
        # persists/recomputes attachments, and returns the out model.
        return _fetch_and_persist_email_content(
            manager, account_label, account_id, provider_message_id,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected email content fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError("Failed to fetch email content.") from exc


def _persist_attachment_metadata(
    account_id: str,
    provider_message_id: str,
    metadata_list: list[Any],
) -> None:
    """Upsert the discovered attachment list into ``email_attachments`` (D-09).

    Soft-fail on persistence: a transient DB error here would block the
    user from reading the body just because the list could not be
    cached. The next ``get_email_content`` call (cache miss again on
    the body side) will retry.
    """
    if not metadata_list:
        return
    # B-SANITIZE: received attachments must go through the same filename
    # sanitisation as drafts (D-20) — neutralise path traversal / reserved
    # characters / reserved Windows names and resolve duplicates within the
    # same message with `` (1)``, `` (2)``. ``mime_type`` is already resolved
    # in the provider client (B-MIME / B-OUTLOOK-LOWER), so it is persisted
    # as-is.
    seen_names: list[str] = []
    rows = []
    for meta in metadata_list:
        safe_name = sanitize_filename(meta.filename, existing=seen_names)
        seen_names.append(safe_name)
        rows.append(
            {
                "attachment_id": str(uuid.uuid4()),
                "account_id": account_id,
                "provider_message_id": provider_message_id,
                "part_id": meta.part_id,
                "provider_attachment_id": meta.provider_attachment_id,
                "filename": safe_name,
                "mime_type": meta.mime_type,
                "size": meta.size,
                "content_id": meta.content_id,
                "is_inline": meta.is_inline,
                "position": meta.position,
            }
        )
    try:
        email_attachment_store.upsert_batch(rows)
    except DatabaseError as exc:
        logger.warning(
            "Attachment metadata upsert failed (%s): %s",
            type(exc).__name__, exc,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected attachment metadata upsert error (%s): %s",
            type(exc).__name__, exc,
        )


def _load_email_attachments_out(
    account_id: str, provider_message_id: str,
) -> list[AttachmentMetadataOut]:
    """List ``email_attachments`` rows mapped to the API output schema.

    Used both at cache hit and after a fresh upsert so the returned
    list always carries the derived ``is_downloaded`` flag (true iff a
    blob row exists). Soft-fails to an empty list — the user still
    sees the email body even if the metadata read hiccups.
    """
    try:
        rows = email_attachment_store.list_by_message(account_id, provider_message_id)
    except DatabaseError as exc:
        logger.warning(
            "Failed to list email attachments for content view (%s): %s",
            type(exc).__name__, exc,
        )
        return []
    except Exception as exc:
        logger.warning(
            "Unexpected error listing email attachments for content view (%s): %s",
            type(exc).__name__, exc,
        )
        return []
    return [
        AttachmentMetadataOut(
            attachment_id=str(row["attachment_id"]),
            filename=str(row.get("filename") or "attachment"),
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            size=int(row.get("size") or 0),
            is_downloaded=bool(row.get("is_downloaded", False)),
            is_unavailable=row.get("unavailable_at") is not None,
            position=int(row.get("position") or 0),
        )
        for row in rows
    ]
