"""
Add source-tracking columns to ``draft_attachments`` for Forward copies.

When the user opens an email and clicks "Forward", the service copies
the original message's downloadable attachments (``is_inline = FALSE``
rows from ``email_attachments``) into ``draft_attachments`` so the
new draft owns its own bytes (D-07 lazy push semantics). The two new
columns let a retry of the copy endpoint stay **idempotent** (R-12):

- ``source_account_id`` — the account that owns the originating email.
  In MVP it equals the draft's ``account_id`` (Forward is locked to the
  same account at the composer), but the column is kept account-scoped
  so a future cross-account Forward stays trivial to add.
- ``source_attachment_id`` — the ``email_attachments.attachment_id`` of
  the originating row. The copy endpoint reads existing
  ``source_attachment_id`` values for the target draft and **skips**
  the ones already copied, so a network hiccup mid-copy + retry does
  not duplicate chips.

Both columns are nullable: rows uploaded directly by the user (drag &
drop, file picker) keep them NULL — they don't originate from any
``email_attachments`` row. The partial index lives only on copied rows
so non-Forward drafts pay nothing for it.

**No FK** to ``email_attachments``: if the original email is later
trashed or hard-deleted, the draft copy must stay valid (the bytes
are already in ``draft_attachments``). A CASCADE FK would erase
chips the user has not yet sent; a NO ACTION FK would block the
email deletion. The columns are a soft reference, same policy as
``drafts.reply_to_*`` (migration 0029).
"""
from __future__ import annotations

from alembic import op


revision = "0030_draft_attachments_source"
down_revision = "0029_drafts_reply_threading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE draft_attachments
            ADD COLUMN IF NOT EXISTS source_account_id UUID;
        """
    )
    op.execute(
        """
        ALTER TABLE draft_attachments
            ADD COLUMN IF NOT EXISTS source_attachment_id UUID;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_draft_attachments_source
            ON draft_attachments (account_id, provider_draft_id, source_attachment_id)
            WHERE source_attachment_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_draft_attachments_source;")
    op.execute("ALTER TABLE draft_attachments DROP COLUMN IF EXISTS source_attachment_id;")
    op.execute("ALTER TABLE draft_attachments DROP COLUMN IF EXISTS source_account_id;")
