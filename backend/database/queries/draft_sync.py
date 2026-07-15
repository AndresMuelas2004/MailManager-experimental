"""
Draft sync job SQL statements (reliable server-side draft sync).

One row per account in ``draft_sync_jobs``. A simplified clone of
``account_backfill.py`` WITHOUT the pagination checkpoint statements: the draft
sync is a single non-paginated operation per account, so there is no
``update_progress`` / ``set_anchor`` and the claimed row carries no cursor.
"""

from __future__ import annotations

# Full projection reused by CLAIM_NEXT_BATCH RETURNING so the store always
# returns a complete row shape the worker can act on.
_COLUMNS = (
    "account_id, mailbox_id, provider, status, attempts, last_error, "
    "created_at, updated_at, completed_at"
)


# Enqueue (or re-enqueue) a draft sync on EVERY connect. Unlike the backfill
# ENQUEUE there is NO ``WHERE status='failed'`` guard: a reconnection (and even a
# re-open) must refresh the drafts, so an existing pending/running/completed/
# failed job is unconditionally reset to ``pending`` with a cleared error. The
# dispatcher's in-flight tracking prevents a concurrent double-run for the same
# account within a process; the atomic full-replace persistence makes a rare
# duplicate run harmless.
ENQUEUE = """
    INSERT INTO draft_sync_jobs (account_id, mailbox_id, provider, status)
    VALUES (%(account_id)s, %(mailbox_id)s, %(provider)s, 'pending')
    ON CONFLICT (account_id) DO UPDATE SET
        status = 'pending',
        last_error = NULL,
        updated_at = now()
"""


# Atomically claim up to ``limit`` pending jobs, marking them running. Single
# worker MVP: no ``FOR UPDATE SKIP LOCKED`` (mirrors account_backfill.CLAIM).
CLAIM_NEXT_BATCH = f"""
    UPDATE draft_sync_jobs
       SET status = 'running', updated_at = now()
     WHERE account_id IN (
         SELECT account_id
         FROM draft_sync_jobs
         WHERE status = 'pending'
         ORDER BY created_at
         LIMIT %(limit)s
     )
    RETURNING {_COLUMNS}
"""


MARK_COMPLETED = """
    UPDATE draft_sync_jobs
       SET status = 'completed',
           completed_at = now(),
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


MARK_FAILED = """
    UPDATE draft_sync_jobs
       SET status = 'failed',
           last_error = %(error)s,
           attempts = attempts + 1,
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


# Startup recovery: a job left 'running' by a previous process crash is put back
# to 'pending' so the dispatcher re-claims it.
RESET_RUNNING_TO_PENDING = """
    UPDATE draft_sync_jobs
       SET status = 'pending', updated_at = now()
     WHERE status = 'running'
"""


# Auto-recovery reaper: revive a 'failed' job (attempts < max) after it has sat
# failed for at least ``backoff_seconds``. Same shape as the backfill reaper.
RESET_RETRIABLE_FAILED_TO_PENDING = """
    UPDATE draft_sync_jobs
       SET status = 'pending', updated_at = now()
     WHERE status = 'failed'
       AND attempts < %(max_attempts)s
       AND updated_at < now() - (%(backoff_seconds)s * INTERVAL '1 second')
"""
