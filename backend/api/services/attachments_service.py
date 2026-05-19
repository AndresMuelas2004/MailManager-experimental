"""
Service layer for attachment download + admin maintenance (D-06, D-15, D-30).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator

logger = logging.getLogger(__name__)

from core.email import (
    AttachmentMetadata,
    CoreError,
    EmailAttachmentNotFound,
)
from database import (
    DatabaseError,
    account_store,
    email_attachment_store,
)

from api.errors.exceptions import (
    AccountNotFound,
    AttachmentNotFound,
    AttachmentProviderUnavailable,
    AttachmentUnavailable,
    DatabaseQueryError,
    InvalidAdminToken,
    PurgeDisabled,
)
from api.schemas.attachment import PurgeResult
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
    unwrap_secret,
)


_DOWNLOAD_STREAM_CHUNK_SIZE = 64 * 1024  # 64 KB chunks for StreamingResponse


def _persist_refreshed_tokens_for_attachment(
    updated_tokens: dict[str, dict[str, Any]],
    label_lookup: dict[str, tuple[str, str, str]],
) -> None:
    """Persist refreshed tokens during an attachment download flow.

    Mirrors ``drafts_service._persist_refreshed_tokens`` but uses
    :py:class:`AttachmentNotFound` as the fallback ApiError so a
    persistence hiccup mid-download surfaces in the right context.
    """
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
                "Unexpected attachment download token refresh persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise DatabaseQueryError(
                "Failed to persist refreshed tokens during attachment download."
            ) from exc


def _stream_blob(blob: bytes) -> Iterator[bytes]:
    """Yield the blob in fixed-size chunks for StreamingResponse.

    psycopg2 already loaded the full BYTEA into memory in a single
    SELECT; this generator just paces the bytes back to the client so
    the FastAPI runtime does not buffer the whole response in a single
    chunk. With a 25 MB cap (D-01) the memory footprint per request is
    bounded and predictable.
    """
    for offset in range(0, len(blob), _DOWNLOAD_STREAM_CHUNK_SIZE):
        yield blob[offset : offset + _DOWNLOAD_STREAM_CHUNK_SIZE]


def download_email_attachment(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    attachment_id: str,
    user_id: str,
) -> tuple[Iterator[bytes], str, str, int]:
    """Cache-aside attachment download (D-06, D-17).

    Returns ``(chunk_iterator, mime_type, filename, size)`` so the
    router can build the StreamingResponse with the right
    ``Content-Disposition`` (via :py:func:`format_content_disposition`)
    and a BackgroundTask that refreshes ``last_accessed_at`` on
    success (D-15).

    Path:
    1. Verify mailbox ownership and locate the attachment row in a
       single ownership-chain JOIN (D-22).
    2. If ``unavailable_at`` is set, raise immediately — no provider
       call, the row is permanently gone (D-17).
    3. If a blob exists locally (``email_attachment_blobs``), serve it.
    4. Otherwise authenticate the provider client, fetch the binary,
       persist it, and serve. Provider 404/410 stamps
       ``unavailable_at``; 403 / persistent 5xx propagate as
       :py:class:`AttachmentProviderForbidden` /
       :py:class:`AttachmentProviderUnavailable` respectively.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        row = email_attachment_store.get_for_download(
            user_id=user_id,
            mailbox_id=mailbox_id,
            account_id=account_id,
            attachment_id=attachment_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected attachment download lookup error (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentNotFound(
            "Attachment lookup failed while serving download for the email viewer."
        ) from exc

    if row is None:
        raise AttachmentNotFound(
            "Attachment not found or not accessible for the authenticated user."
        )

    if row.get("unavailable_at") is not None:
        raise AttachmentUnavailable(
            f"Attachment {attachment_id} marked unavailable by provider."
        )

    if row.get("provider_message_id") != provider_message_id:
        raise AttachmentNotFound(
            "Attachment does not belong to the requested provider_message_id."
        )

    # Try the local cache first.
    try:
        blob = email_attachment_store.get_blob(attachment_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected attachment blob load error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DatabaseQueryError(
            "Failed to read cached attachment blob."
        ) from exc

    if blob is None:
        blob = _fetch_and_cache_blob(
            mailbox_id=mailbox_id,
            account_id=account_id,
            row=row,
            attachment_id=attachment_id,
        )

    filename = str(row.get("filename") or "attachment")
    mime_type = str(row.get("mime_type") or "application/octet-stream")
    return _stream_blob(blob), mime_type, filename, len(blob)


def _fetch_and_cache_blob(
    *,
    mailbox_id: str,
    account_id: str,
    row: dict[str, Any],
    attachment_id: str,
) -> bytes:
    """Cache-miss branch: pull the binary from the provider and persist.

    Catches :py:class:`EmailAttachmentNotFound` to stamp
    ``unavailable_at`` (D-17) and translates it to
    :py:class:`AttachmentUnavailable` so the response carries the
    ``attachment_unavailable`` code.
    """
    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during attachment fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentProviderUnavailable(
            "Failed to load account record while fetching attachment."
        ) from exc
    if account is None:
        raise AccountNotFound(
            "Account not found while fetching attachment from the provider."
        )

    provider = str(account.get("provider") or "").lower()
    account_label = f"{mailbox_id}__{account_id}"
    label_lookup = {account_label: (mailbox_id, account_id, provider)}

    app_credentials = load_wrapped_app_credentials(provider)
    user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
    auth_payloads = {account_label: (app_credentials, user_tokens)}

    manager = build_manager_for_accounts([account])
    try:
        updated_tokens = manager.authenticate_all_silent(auth_payloads)
    except CoreError as exc:
        raise translate_core_error(exc, fallback=AttachmentProviderUnavailable) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected silent auth error during attachment fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentProviderUnavailable(
            "Silent authentication failed while fetching attachment."
        ) from exc

    raise_on_silent_auth_errors(
        manager.get_last_errors(), fallback=AttachmentProviderUnavailable,
    )
    if updated_tokens:
        _persist_refreshed_tokens_for_attachment(updated_tokens, label_lookup)

    attachment_meta = AttachmentMetadata(
        provider_message_id=str(row.get("provider_message_id") or ""),
        part_id=row.get("part_id"),
        provider_attachment_id=row.get("provider_attachment_id"),
        filename=str(row.get("filename") or "attachment"),
        mime_type=str(row.get("mime_type") or "application/octet-stream"),
        size=int(row.get("size") or 0),
        content_id=row.get("content_id"),
        is_inline=bool(row.get("is_inline", False)),
        position=int(row.get("position") or 0),
    )

    try:
        binary = manager.fetch_attachment_binary(
            account_label,
            attachment_meta.provider_message_id,
            attachment_meta,
        )
    except EmailAttachmentNotFound as exc:
        # Intentional best-effort: the user-visible signal is the 404/410 we
        # already received from the provider. Stamping ``unavailable_at`` is
        # a TTL/back-pressure hint — if the DB write itself blows up, log it
        # but do not mask the AttachmentUnavailable response. Both branches
        # therefore swallow their own failure (uniform soft-fail).
        try:
            email_attachment_store.mark_unavailable(attachment_id)
        except DatabaseError as db_exc:
            logger.warning(
                "mark_unavailable failed (DB) for attachment %s: %s",
                attachment_id, db_exc,
            )
        except Exception as db_exc:
            logger.warning(
                "Unexpected mark_unavailable error (%s): %s",
                type(db_exc).__name__, db_exc,
            )
        raise AttachmentUnavailable(
            f"Provider returned 404/410 for attachment {attachment_id}; marking unavailable."
        ) from exc
    except CoreError as exc:
        raise translate_core_error(exc, fallback=AttachmentProviderUnavailable) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected attachment provider fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentProviderUnavailable(
            "Provider attachment fetch failed unexpectedly."
        ) from exc

    try:
        email_attachment_store.insert_blob(attachment_id, binary.data)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected attachment blob persist error (%s): %s",
            type(exc).__name__, exc,
        )
        raise AttachmentProviderUnavailable(
            "Failed to persist downloaded attachment blob."
        ) from exc

    return binary.data


def touch_attachment_last_accessed(attachment_id: str) -> None:
    """BackgroundTask body: refresh ``last_accessed_at`` after a successful stream.

    Wrapped in a try/except so a transient DB hiccup does not surface
    to the user — this is a TTL hint, not a correctness guarantee.
    """
    try:
        email_attachment_store.touch_last_accessed(attachment_id)
    except Exception as exc:
        logger.warning(
            "Failed to touch last_accessed_at for attachment %s (%s): %s",
            attachment_id, type(exc).__name__, exc,
        )


# ---------------------------------------------------------------------------
# Admin: manual purge of expired blobs (D-30).
# ---------------------------------------------------------------------------


_PURGE_TOKEN_ENV_VAR = "ATTACHMENTS_PURGE_TOKEN"


def purge_expired_attachments(provided_token: str | None) -> PurgeResult:
    """Purge ``email_attachment_blobs`` rows past the 30-day TTL (D-15, D-30).

    Auth is intentionally minimal: an env-var token. The endpoint is
    not for end users — only an operator with shell access can grant
    permission. Three states:

    - The env var is absent → :py:class:`PurgeDisabled` (HTTP 503,
      code ``purge_disabled``). The deploy was not configured for
      this operation; treat as a no-op rather than 401, so misuse
      does not look like a credential issue.
    - The header is missing or wrong →
      :py:class:`InvalidAdminToken` (HTTP 401).
    - Otherwise execute the purge and return the stats.
    """
    expected = os.getenv(_PURGE_TOKEN_ENV_VAR)
    if not expected:
        raise PurgeDisabled(
            f"Attachment purge endpoint disabled: {_PURGE_TOKEN_ENV_VAR} is not set."
        )
    if not provided_token or provided_token != expected:
        raise InvalidAdminToken(
            "Invalid X-Admin-Token header for /admin/attachments/purge."
        )
    try:
        purged_count, freed_bytes = email_attachment_store.purge_expired_blobs()
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected purge error (%s): %s", type(exc).__name__, exc,
        )
        raise DatabaseQueryError(
            "Failed to purge expired attachment blobs."
        ) from exc
    return PurgeResult(purged_count=purged_count, freed_bytes=freed_bytes)


