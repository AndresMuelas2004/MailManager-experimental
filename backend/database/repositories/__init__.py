"""
Concrete repository exports.
"""

from __future__ import annotations

from database.repositories.account_backfill_repository import account_backfill_store
from database.repositories.account_repository import account_store
from database.repositories.draft_attachment_repository import draft_attachment_store
from database.repositories.draft_repository import draft_store
from database.repositories.draft_sync_repository import draft_sync_store
from database.repositories.email_attachment_repository import email_attachment_store
from database.repositories.email_content_repository import email_content_store
from database.repositories.email_metadata_repository import email_metadata_store
from database.repositories.mailbox_repository import mailbox_store
from database.repositories.session_repository import session_store
from database.repositories.user_repository import user_store
from database.repositories.virtual_mailbox_repository import virtual_mailbox_store

__all__ = [
    "account_backfill_store",
    "account_store",
    "draft_attachment_store",
    "draft_store",
    "draft_sync_store",
    "email_attachment_store",
    "email_content_store",
    "email_metadata_store",
    "mailbox_store",
    "session_store",
    "user_store",
    "virtual_mailbox_store",
]
