"""
Rule-apply job SQL ("apply to existing", carpetas-y-reglas).

One row per RULE in ``rule_apply_jobs`` — the durable queue driven by the SAME
in-process worker as ``account_backfill_jobs`` / ``draft_sync_jobs``. Unlike
those two (per-account, single provider), a rule apply is per-rule / per-user and
spans every Gmail AND Outlook account of the user, so the PK is ``rule_id`` and
``owner_user_id`` is denormalised (cascades via ``rules``). The row is a
resumable checkpoint (``page_cursor`` = last seen ``(received_at, account_id,
provider_message_id)`` tuple + ``processed_count``).
"""

from __future__ import annotations


_COLUMNS = (
    "rule_id, owner_user_id, status, page_cursor, processed_count, "
    "attempts, last_error, created_at, updated_at, completed_at"
)


# Enqueue (or re-enqueue) an apply. Like the draft-sync ENQUEUE — and UNLIKE the
# backfill — this is UNCONDITIONAL: every "apply to existing" click restarts a
# full scan, so an existing row is reset to ``pending`` with the checkpoint
# cleared (``page_cursor = NULL``, ``processed_count = 0``). Assignment is
# idempotent (provider add + ON CONFLICT DO NOTHING member insert), so a
# re-scan is safe. Enqueue is INDEPENDENT of ``BACKFILL_WORKER_ENABLED`` (there
# is no fallback path) — the job simply waits ``pending`` until the worker runs.
ENQUEUE = """
    INSERT INTO rule_apply_jobs (rule_id, owner_user_id, status)
    VALUES (%(rule_id)s, %(owner_user_id)s, 'pending')
    ON CONFLICT (rule_id) DO UPDATE SET
        status = 'pending',
        page_cursor = NULL,
        processed_count = 0,
        last_error = NULL,
        updated_at = now()
"""


GET = f"""
    SELECT {_COLUMNS}
    FROM rule_apply_jobs
    WHERE rule_id = %(rule_id)s
"""


# Atomically claim up to ``limit`` pending jobs, marking them running. Single
# worker MVP: no ``FOR UPDATE SKIP LOCKED`` (mirrors the sibling queues).
CLAIM_NEXT_BATCH = f"""
    UPDATE rule_apply_jobs
       SET status = 'running', updated_at = now()
     WHERE rule_id IN (
         SELECT rule_id
         FROM rule_apply_jobs
         WHERE status = 'pending'
         ORDER BY created_at
         LIMIT %(limit)s
     )
    RETURNING {_COLUMNS}
"""


UPDATE_PROGRESS = """
    UPDATE rule_apply_jobs
       SET processed_count = %(processed_count)s,
           page_cursor = %(page_cursor)s,
           updated_at = now()
     WHERE rule_id = %(rule_id)s
"""


MARK_COMPLETED = """
    UPDATE rule_apply_jobs
       SET status = 'completed',
           completed_at = now(),
           page_cursor = NULL,
           updated_at = now()
     WHERE rule_id = %(rule_id)s
"""


MARK_FAILED = """
    UPDATE rule_apply_jobs
       SET status = 'failed',
           last_error = %(error)s,
           attempts = attempts + 1,
           updated_at = now()
     WHERE rule_id = %(rule_id)s
"""


# Startup recovery: a job left 'running' by a previous process crash is put back
# to 'pending' so the dispatcher re-claims and RESUMES it from its checkpoint.
RESET_RUNNING_TO_PENDING = """
    UPDATE rule_apply_jobs
       SET status = 'pending', updated_at = now()
     WHERE status = 'running'
"""


# Auto-recovery reaper: revive a 'failed' job (attempts < max) after the
# cool-off — resumes from its checkpoint (page_cursor + processed_count are
# preserved by MARK_FAILED). Same shape as the sibling queues' reapers.
RESET_RETRIABLE_FAILED_TO_PENDING = """
    UPDATE rule_apply_jobs
       SET status = 'pending', updated_at = now()
     WHERE status = 'failed'
       AND attempts < %(max_attempts)s
       AND updated_at < now() - (%(backoff_seconds)s * INTERVAL '1 second')
"""
