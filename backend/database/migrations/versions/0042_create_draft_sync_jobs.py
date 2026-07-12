"""
Create the ``draft_sync_jobs`` table (reliable server-side draft sync).

One row per account, a simplified clone of ``account_backfill_jobs`` WITHOUT the
pagination checkpoint columns (``page_cursor`` / ``target_total`` /
``fetched_count``): the draft sync is a single, non-paginated operation per
account (one ``fetch_all_drafts`` + one ``replace_all_for_account`` of up to 500
drafts). The in-process worker's dispatcher claims and runs these jobs in the
same pool as the backfill jobs; the reaper revives a ``failed`` one for retry.

Enqueued on EVERY connect (first connection and reconnection) so the drafts are
always refreshed — unlike the backfill, which only enqueues on the first
connection. The ``account_id`` PK + ``ON DELETE CASCADE`` mirror the backfill
table (a FK must match the referenced ``accounts.account_id`` UUID type);
``mailbox_id`` is a denormalised UUID (no FK — the account already cascades) so
the worker rebuilds ``account_label`` without a JOIN. The partial index backs
the dispatcher's poll for active (pending/running) jobs.
"""
from __future__ import annotations

from alembic import op


revision = "0042_create_draft_sync_jobs"
down_revision = "0041_create_account_backfill_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_sync_jobs (
            account_id   UUID         PRIMARY KEY
                         REFERENCES accounts(account_id) ON DELETE CASCADE,
            mailbox_id   UUID         NOT NULL,
            provider     VARCHAR(20)  NOT NULL
                         CHECK (provider IN ('gmail', 'outlook')),
            status       VARCHAR(20)  NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            attempts     INTEGER      NOT NULL DEFAULT 0,
            last_error   TEXT,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_draft_sync_jobs_active "
        "ON draft_sync_jobs (status) "
        "WHERE status IN ('pending', 'running');"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_draft_sync_jobs_active;")
    op.execute("DROP TABLE IF EXISTS draft_sync_jobs;")
