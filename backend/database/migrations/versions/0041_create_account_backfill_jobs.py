"""
Create the ``account_backfill_jobs`` table (background initial mass backfill).

One row per account. Drives the in-process background worker that downloads
up to ``BACKFILL_MAX_EMAILS_PER_ACCOUNT`` (default 100k) message headers per
newly connected account, in resumable waves, replacing the old synchronous
500-message bootstrap.

The row is the checkpoint: ``page_cursor`` (Gmail ``messages.list`` pageToken /
Outlook ``@odata.nextLink``) + ``fetched_count`` let a job resume from where it
left off after a process restart without duplicating or restarting from zero.
``initial_sync_cursor`` snapshots the incremental cursor (Gmail historyId /
Outlook per-folder delta links) captured at the START of the backfill, written
into ``accounts.sync_cursor`` on completion so the classic incremental sync
resumes exactly from there.

``account_id`` is ``UUID`` (NOT ``TEXT``): a FK must declare the same type as
the referenced column, and ``accounts.account_id`` is ``UUID``. ``ON DELETE
CASCADE`` removes the job when the account is deleted. ``mailbox_id`` is a
denormalised ``UUID`` (no FK of its own — the account already cascades) so the
worker can rebuild ``account_label = f"{mailbox_id}__{account_id}"`` without a
JOIN. The partial index backs the dispatcher's poll for active jobs.
"""
from __future__ import annotations

from alembic import op


revision = "0041_create_account_backfill_jobs"
down_revision = "0040_invalidate_email_content_cache_allowlist_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_backfill_jobs (
            account_id           UUID         PRIMARY KEY
                                 REFERENCES accounts(account_id) ON DELETE CASCADE,
            mailbox_id           UUID         NOT NULL,
            provider             VARCHAR(20)  NOT NULL
                                 CHECK (provider IN ('gmail', 'outlook')),
            status               VARCHAR(20)  NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            target_total         INTEGER      NOT NULL,
            fetched_count        INTEGER      NOT NULL DEFAULT 0,
            page_cursor          TEXT,
            initial_sync_cursor  TEXT,
            attempts             INTEGER      NOT NULL DEFAULT 0,
            last_error           TEXT,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            completed_at         TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_backfill_jobs_active "
        "ON account_backfill_jobs (status) "
        "WHERE status IN ('pending', 'running');"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_backfill_jobs_active;")
    op.execute("DROP TABLE IF EXISTS account_backfill_jobs;")
