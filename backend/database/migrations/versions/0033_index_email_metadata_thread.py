"""
Add a composite index on email_metadata(account_id, thread_id, received_at DESC)
to back the conversation grouping listing (DISTINCT ON / window functions over
the thread key) introduced by the conversation view.

The grouping key is COALESCE(NULLIF(thread_id, ''), provider_message_id), which
is not directly indexable without a functional index; this index covers the
common case (rows whose thread_id is non-empty). Threadless rows fall back to
their provider_message_id, already covered by the table primary key. The index
is an optimisation, not a correctness requirement — the grouped queries run
without it (falling back to seq-scan + sort).
"""
from __future__ import annotations

from alembic import op


revision = "0033_index_email_metadata_thread"
down_revision = "0032_drop_virtual_mailbox_scope_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_metadata_account_thread "
        "ON email_metadata (account_id, thread_id, received_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_email_metadata_account_thread;")
