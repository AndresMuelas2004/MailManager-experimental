"""
Add reply / forward threading columns to the ``drafts`` table.

The composer can now open a draft as Reply / Reply All / Forward, and
the draft has to remember three orthogonal pieces of information:

1. **What kind of draft it is** — ``reply_kind`` (``reply`` / ``reply_all``
   / ``forward``) lets the send path inject the right wire-level
   threading metadata and lets the UI re-open the draft in the right
   mode after "Save and close" (D-28).
2. **Which message it's responding to** — ``reply_to_message_id`` (the
   provider's id of the original) plus ``reply_to_account_id`` (the
   account that owns it, possibly different from the draft's account
   for a cross-account Forward — defensive shape, MVP locks them to
   the same account).
3. **How the provider stitches the thread** — ``thread_id`` (Gmail
   ``threadId`` / Outlook ``conversationId``) plus the RFC 5322
   ``In-Reply-To`` and ``References`` headers. Gmail needs all three
   pieces simultaneously (see the triple-requirement note in
   repository_guide.md); Outlook uses ``createReply/All/Forward``
   server-side so the strings are persisted defensively for future
   re-hydration but not used in the send path.

All columns are nullable — drafts that pre-date this migration, and
new "compose from scratch" drafts, leave them ``NULL``. Drafts that
came from the provider via ``sync_drafts`` also keep them ``NULL``
because the provider does not expose them as draft properties.

**No FK** from ``reply_to_message_id`` / ``reply_to_account_id`` to
``email_metadata``: if the original email is later trashed or
hard-deleted, the draft must stay valid (the user may already have
written the body). A CASCADE FK would erase the draft; a NO ACTION
FK would block the email deletion. The columns are a soft reference
that the service layer interprets defensively.

The partial index keys ``(reply_to_account_id, reply_to_message_id)``
to support future "list drafts that reply to <message>" lookups
(e.g. for a hypothetical "you already drafted a reply" hint in the
viewer). ``WHERE reply_to_message_id IS NOT NULL`` keeps the index
small — only reply-derived drafts pay for it.
"""
from __future__ import annotations

from alembic import op


revision = "0029_drafts_reply_threading"
down_revision = "0028_create_virtual_mailboxes_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS reply_kind VARCHAR(20)
                CHECK (reply_kind IN ('reply', 'reply_all', 'forward'));
        """
    )
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS reply_to_message_id VARCHAR(255);
        """
    )
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS reply_to_account_id UUID;
        """
    )
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS thread_id VARCHAR(255);
        """
    )
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS in_reply_to VARCHAR(998);
        """
    )
    op.execute(
        """
        ALTER TABLE drafts
            ADD COLUMN IF NOT EXISTS references_header TEXT;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_drafts_reply_to_message_id
            ON drafts (reply_to_account_id, reply_to_message_id)
            WHERE reply_to_message_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_drafts_reply_to_message_id;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS references_header;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS in_reply_to;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS thread_id;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS reply_to_account_id;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS reply_to_message_id;")
    op.execute("ALTER TABLE drafts DROP COLUMN IF EXISTS reply_kind;")
