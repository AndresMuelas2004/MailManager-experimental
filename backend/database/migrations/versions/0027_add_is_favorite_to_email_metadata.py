"""
Add ``is_favorite`` to ``email_metadata``.

``is_favorite`` is the cross-provider abstraction over Gmail's
``STARRED`` label and Outlook's message ``flag`` follow-up status. It
is ORTHOGONAL to ``box`` / ``is_read`` (a message can be in ``INBOX``,
``SPAM`` or ``TRASH`` and simultaneously be a favourite — both
providers model it that way).

A partial index covers the favourites-only listing query
(``WHERE is_favorite = TRUE`` AND optional ``box NOT IN ('TRASH','SPAM')``);
keeping it partial avoids bloating the index with the (much larger)
``is_favorite = FALSE`` majority.
"""
from __future__ import annotations

from alembic import op


revision = "0027_add_is_favorite_to_email_metadata"
down_revision = "0026_index_email_attachments_last_accessed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE email_metadata "
        "ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_metadata_favorites "
        "ON email_metadata (account_id, received_at DESC) "
        "WHERE is_favorite = TRUE;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_email_metadata_favorites;")
    op.execute("ALTER TABLE email_metadata DROP COLUMN IF EXISTS is_favorite;")
