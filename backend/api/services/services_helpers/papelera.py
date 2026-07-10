"""Operaciones de papelera: mover a papelera, marcar como borrado y restaurar."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError
from database import email_metadata_store, DatabaseError

from .traduccion_errores import translate_database_error


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
