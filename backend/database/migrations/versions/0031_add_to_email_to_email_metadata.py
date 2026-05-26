"""
Add ``to_email`` / ``to_name`` (first recipient of the ``To`` header)
to ``email_metadata``.

Symmetric with the existing ``from_email`` / ``from_name`` pair —
captured during sync so the inbox listing can render the destination
without re-hitting the provider on every render.

Why store only the **first** ``To`` recipient: the inbox table has a
single column for "Para"; multi-recipient messages still happen but
the most common reading semantics (show "to whom this email went")
are well served by the primary destination, mirroring what Gmail /
Outlook web themselves render in their list views. A future "list
detail" rework could promote this to ``TEXT[]`` — the column shape
makes that a non-destructive migration.

Nullable defaults (``''``) so existing rows survive the ALTER without
a backfill round trip; the next metadata sync repopulates them.
"""
from __future__ import annotations

from alembic import op


revision = "0031_add_to_email_to_email_metadata"
down_revision = "0030_draft_attachments_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE email_metadata
            ADD COLUMN IF NOT EXISTS to_email VARCHAR(320) NOT NULL DEFAULT '';
        """
    )
    op.execute(
        """
        ALTER TABLE email_metadata
            ADD COLUMN IF NOT EXISTS to_name VARCHAR(200) NOT NULL DEFAULT '';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE email_metadata DROP COLUMN IF EXISTS to_name;")
    op.execute("ALTER TABLE email_metadata DROP COLUMN IF EXISTS to_email;")
