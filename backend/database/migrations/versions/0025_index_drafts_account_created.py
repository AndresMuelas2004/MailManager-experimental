"""
Add index on drafts(account_id, created_at DESC) to back the listing query.

The two listing queries (``LIST_DRAFTS_BY_ACCOUNT`` and ``LIST_DRAFTS_BY_MAILBOX``)
filter by ``account_id`` and order by ``created_at DESC``. Without a matching
composite index PostgreSQL falls back to a sequential scan + sort, which is
fine while the table is tiny but degrades sharply once a workspace
accumulates drafts across multiple accounts. The composite index covers
both filtering and ordering in a single B-tree walk.
"""
from __future__ import annotations

from alembic import op


revision = "0025_index_drafts_account_created"
down_revision = "0024_invalidate_email_content_cache_attachments_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL because the second column needs ``DESC`` in its index
    # expression — Alembic's ``op.create_index`` only accepts column-name
    # strings without a sort direction modifier.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_drafts_account_created "
        "ON drafts (account_id, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_drafts_account_created;")
