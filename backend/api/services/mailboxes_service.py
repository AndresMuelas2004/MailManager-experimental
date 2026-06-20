"""
Service layer for mailbox operations.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from api.errors.exceptions import MailboxNotFound, MailboxOperationError
from api.schemas.mailbox import MailboxCreate, MailboxOut, MailboxUpdate
from database import DatabaseError, mailbox_store
from api.services.services_helpers import ensure_mailbox_access, translate_database_error

logger = logging.getLogger(__name__)


def create_mailbox(payload: MailboxCreate, user_id: str) -> MailboxOut:
    record = {
        "mailbox_id": str(uuid4()),
        "display_name": payload.display_name,
        "owner_user_id": user_id,
    }
    try:
        created = mailbox_store.create(record)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mailbox creation error (%s): %s", type(exc).__name__, exc)
        raise MailboxOperationError("Failed to create mailbox.") from exc
    return MailboxOut(**created)


def list_mailboxes(user_id: str) -> list[MailboxOut]:
    try:
        mailboxes = mailbox_store.list_by_owner(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mailbox listing error (%s): %s", type(exc).__name__, exc)
        raise MailboxOperationError("Failed to list mailboxes.") from exc
    return [MailboxOut(**mailbox) for mailbox in mailboxes]


def get_mailbox(mailbox_id: str, user_id: str) -> MailboxOut:
    record = ensure_mailbox_access(mailbox_id, user_id)
    return MailboxOut(**record)


def update_mailbox(mailbox_id: str, payload: MailboxUpdate, user_id: str) -> MailboxOut:
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        updated = mailbox_store.update(mailbox_id, payload.display_name)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mailbox rename error (%s): %s", type(exc).__name__, exc)
        raise MailboxOperationError("Failed to rename mailbox.") from exc
    if updated is None:
        # Race: the row passed the ownership pre-check but was deleted
        # before the UPDATE ran. Surface a 404 instead of a silent 200.
        raise MailboxNotFound(f"Mailbox '{mailbox_id}' not found while renaming.")
    return MailboxOut(**updated)


def delete_mailbox(mailbox_id: str, user_id: str) -> dict[str, str]:
    ensure_mailbox_access(mailbox_id, user_id)
    # ON DELETE CASCADE removes associated accounts and tokens automatically.
    try:
        deleted = mailbox_store.delete(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected mailbox deletion error (%s): %s", type(exc).__name__, exc)
        raise MailboxOperationError("Failed to delete mailbox.") from exc
    if not deleted:
        # Race: the row passed the ownership pre-check but was deleted
        # before the DELETE ran. Surface a 404 instead of a silent 200.
        raise MailboxNotFound(f"Mailbox '{mailbox_id}' not found while deleting.")
    return {"status": "deleted"}
