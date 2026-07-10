"""
Account backfill job SQL statements (background initial mass backfill).

One row per account in ``account_backfill_jobs``. The row is the resumable
checkpoint driven by the in-process backfill worker.
"""

from __future__ import annotations

# Full projection reused by GET / LIST_BY_MAILBOX / CLAIM_NEXT_BATCH RETURNING
# so the store always returns a complete row shape.
_COLUMNS = (
    "account_id, mailbox_id, provider, status, target_total, fetched_count, "
    "page_cursor, initial_sync_cursor, attempts, last_error, "
    "created_at, updated_at, completed_at"
)


# Create the job on first connection, or revive a previously FAILED one. The
# WHERE on the DO UPDATE protects an existing pending/running/completed job
# from being reset — a completed account already has its history and must not
# be re-backfilled; a running/pending one is untouched (no-op). Only a failed
# job is revived to pending with a cleared error. A no-op INSERT (row exists,
# not failed) affects zero rows and is a valid outcome — the store does NOT
# check rowcount here.
ENQUEUE = """
    INSERT INTO account_backfill_jobs (account_id, mailbox_id, provider, target_total, status)
    VALUES (%(account_id)s, %(mailbox_id)s, %(provider)s, %(target_total)s, 'pending')
    ON CONFLICT (account_id) DO UPDATE SET
        status = 'pending',
        target_total = EXCLUDED.target_total,
        last_error = NULL,
        updated_at = now()
    WHERE account_backfill_jobs.status = 'failed'
"""


GET = f"""
    SELECT {_COLUMNS}
    FROM account_backfill_jobs
    WHERE account_id = %(account_id)s
"""


LIST_BY_MAILBOX = f"""
    SELECT {_COLUMNS}
    FROM account_backfill_jobs
    WHERE mailbox_id = %(mailbox_id)s
"""


# Only the account_ids of active (pending/running) jobs — used by the sync
# guard to exclude accounts under active backfill without pulling full rows.
LIST_ACTIVE_ACCOUNT_IDS = """
    SELECT account_id
    FROM account_backfill_jobs
    WHERE mailbox_id = %(mailbox_id)s
      AND status IN ('pending', 'running')
"""


# Atomically claim up to ``limit`` pending jobs, marking them running. No
# ``FOR UPDATE SKIP LOCKED`` in the single-worker MVP — the sole dispatcher
# thread claims serially and tracks in-flight jobs itself. The migration
# boundary to a multi-worker deployment is this statement.
CLAIM_NEXT_BATCH = f"""
    UPDATE account_backfill_jobs
       SET status = 'running', updated_at = now()
     WHERE account_id IN (
         SELECT account_id
         FROM account_backfill_jobs
         WHERE status = 'pending'
         ORDER BY created_at
         LIMIT %(limit)s
     )
    RETURNING {_COLUMNS}
"""


UPDATE_PROGRESS = """
    UPDATE account_backfill_jobs
       SET fetched_count = %(fetched_count)s,
           page_cursor = %(page_cursor)s,
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


SET_ANCHOR = """
    UPDATE account_backfill_jobs
       SET initial_sync_cursor = %(initial_sync_cursor)s,
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


MARK_COMPLETED = """
    UPDATE account_backfill_jobs
       SET status = 'completed',
           completed_at = now(),
           page_cursor = NULL,
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


MARK_FAILED = """
    UPDATE account_backfill_jobs
       SET status = 'failed',
           last_error = %(error)s,
           attempts = attempts + 1,
           updated_at = now()
     WHERE account_id = %(account_id)s
"""


# Startup recovery: a job left 'running' by a previous process crash is put
# back to 'pending' so the dispatcher re-claims and resumes it from its
# checkpoint.
RESET_RUNNING_TO_PENDING = """
    UPDATE account_backfill_jobs
       SET status = 'pending', updated_at = now()
     WHERE status = 'running'
"""
