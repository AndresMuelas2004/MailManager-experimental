"""
Add partial index on email_attachments(last_accessed_at) for the TTL purge.

The admin purge endpoint (D-30) filters ``email_attachments`` on
``last_accessed_at IS NOT NULL AND last_accessed_at < (now() - INTERVAL
'30 days')``. Without an index PostgreSQL falls back to a sequential
scan on every manual purge. This is acceptable while the table is
small but becomes expensive once a workspace accumulates hundreds of
thousands of rows. The partial index covers exactly the rows the
purge predicate can match (``WHERE last_accessed_at IS NOT NULL``),
keeping it small.
"""
from __future__ import annotations

from alembic import op


revision = "0026_index_email_attachments_last_accessed"
down_revision = "0025_index_drafts_account_created"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_attachments_last_accessed "
        "ON email_attachments (last_accessed_at) "
        "WHERE last_accessed_at IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_email_attachments_last_accessed;")
