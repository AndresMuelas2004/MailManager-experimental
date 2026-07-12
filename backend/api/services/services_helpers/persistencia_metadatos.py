"""Persistencia y actualizacion de metadatos de correo y cursores de sincronizacion."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from core.email import DraftMetadata, EmailMetadata, LabelUpdate, SpamMoveResult
from database import (
    account_store,
    email_metadata_store,
    DatabaseError,
)

from .traduccion_errores import translate_database_error


def build_draft_rows(drafts: list[DraftMetadata]) -> list[dict]:
    """Map provider ``DraftMetadata`` to the row dicts ``replace_all_for_account``
    persists. Shared by ``drafts_service`` (HTTP draft sync) and the background
    draft-sync worker so the row shape stays in lockstep across both callers."""
    return [
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
            m.is_favorite, m.to_email, m.to_name,
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
    """Update is_read, box and (COALESCEd) is_favorite for specific messages.

    ``lu.is_favorite`` may be ``None`` (Outlook partial delta with no ``flag``),
    in which case ``UPDATE_LABELS_BATCH`` keeps the stored favourite untouched.
    Returns rows updated.
    """
    if not label_updates:
        return 0
    rows = [
        (lu.provider_message_id, account_id, lu.is_read, lu.box, lu.is_favorite)
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


def update_email_read_status_by_thread(
    account_id: str,
    message_ids: list[str],
    is_read: bool,
    *,
    fallback: type[ApiError] = ApiError,
) -> int:
    """Update is_read for every row of the threads ``message_ids`` belong to
    (plus the ids themselves). Used by the conversation viewer so Outlook's
    duplicate rows — the same message persisted under different REST ids by the
    sync vs the conversation fetch — all flip together and the grouped listing
    row stops showing as unread. Returns rows updated."""
    if not message_ids:
        return 0
    try:
        return email_metadata_store.update_read_status_by_thread(
            account_id, message_ids, is_read,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected read status by-thread DB update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback("Failed to update email read status by thread in database.") from exc


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
