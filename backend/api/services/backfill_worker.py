"""In-process background worker for the initial mass backfill + draft sync.

A single daemon dispatcher thread polls ``account_backfill_jobs`` and
``draft_sync_jobs`` for pending work and runs each through a bounded
``ThreadPoolExecutor``. Backfill jobs paginate the provider in rate-paced,
resumable waves, persisting each wave and checkpointing progress so a process
restart resumes from where it left off. Draft-sync jobs are quick, single-shot
(one ``fetch_all_drafts`` + one atomic ``replace_all_for_account``) — claimed
first each poll so drafts stay responsive.

Every job DB WRITE passes through ``_DB_WRITE_GATE`` (a bounded semaphore) so the
parallel backfill never starves the connection pool of the connections user
requests need — psycopg2's ``getconn()`` raises rather than waits on exhaustion,
so the worker waits on the semaphore instead. A failed job is auto-revived for a
bounded number of attempts by the reaper each poll.

No Kafka/Celery/Redis — worker in-process + PostgreSQL (single uvicorn worker,
MVP), same profile as ``api/rate_limit.py``. Started/stopped from the app
lifespan; failures there are best-effort and never abort app startup.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    ApiError,
    BackfillJobError,
    DatabaseConnectionError,
    DraftSyncError,
)
from api.services.backfill_config import (
    backfill_db_write_concurrency,
    backfill_gmail_gets_per_minute,
    backfill_max_attempts,
    backfill_max_concurrent,
    backfill_outlook_page_delay_ms,
    backfill_poll_interval_s,
    is_backfill_worker_enabled,
)
from api.services.services_helpers import (
    build_draft_rows,
    build_manager_for_accounts,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    persist_email_metadata_batch,
    translate_database_error,
    unwrap_secret,
    update_sync_cursor,
)
from core.email import EmailExternalAPIError, retry_with_backoff
from database import (
    ConnectionPoolError,
    DatabaseError,
    account_backfill_store,
    account_store,
    draft_store,
    draft_sync_store,
)

T = TypeVar("T")


# Wave-level retry (on top of the provider clients' own inner batch/transport
# retries): honours Retry-After for Outlook via the transport; for Gmail it is
# the fixed-backoff catch-all around the (non-retrying) messages.list call.
_WAVE_RETRY_ATTEMPTS = 5
_WAVE_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0)

# Provider page-size caps (Gmail messages.list max 500; Graph $top max 1000).
_GMAIL_PAGE_SIZE = 500
_OUTLOOK_PAGE_SIZE = 1000

# Cool-off (seconds) before the reaper revives a failed job — avoids a tight
# retry loop. Passed as the ``backoff_seconds`` of the reaper query.
_FAILED_RETRY_BACKOFF_S = 60

# Brief retry when a gated DB write still hits pool exhaustion (a burst of user
# requests racing the backfill): wait and retry rather than failing the job.
_DB_WRITE_RETRY_ATTEMPTS = 3
_DB_WRITE_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0)

# On shutdown the dispatcher stops claiming and abandons in-flight waves (their
# checkpoint resumes them next start) — it does NOT block on hours-long jobs.
_DISPATCHER_JOIN_TIMEOUT_S = 10.0


_worker_lock = threading.Lock()
_dispatcher_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None

# Bounds concurrent backfill/draft DB writes well under DB_POOL_MAX_CONN so the
# worker never starves the pool. Initialised in ``_dispatcher_loop`` (not at
# import) so BACKFILL_DB_WRITE_CONCURRENCY is read after ``.env`` is loaded.
_DB_WRITE_GATE: threading.BoundedSemaphore | None = None


# ---------------------------------------------------------------------------
# Lifecycle (called from the app lifespan)
# ---------------------------------------------------------------------------


def start_backfill_worker() -> None:
    """Start the dispatcher thread, unless disabled by ``BACKFILL_WORKER_ENABLED``."""
    if not is_backfill_worker_enabled():
        logger.info("Backfill worker disabled (BACKFILL_WORKER_ENABLED not truthy).")
        return
    global _dispatcher_thread, _stop_event
    with _worker_lock:
        if _dispatcher_thread is not None and _dispatcher_thread.is_alive():
            return
        _stop_event = threading.Event()
        _dispatcher_thread = threading.Thread(
            target=_dispatcher_loop,
            args=(_stop_event,),
            name="backfill-dispatcher",
            daemon=True,
        )
        _dispatcher_thread.start()
    logger.info("Backfill worker started.")


def stop_backfill_worker() -> None:
    """Signal the dispatcher to stop and join it (bounded)."""
    global _dispatcher_thread, _stop_event
    with _worker_lock:
        thread, event = _dispatcher_thread, _stop_event
        _dispatcher_thread, _stop_event = None, None
    if event is not None:
        event.set()
    if thread is not None:
        thread.join(timeout=_DISPATCHER_JOIN_TIMEOUT_S)


# ---------------------------------------------------------------------------
# DB-write gate (concurrency bound + pool-exhaustion brief retry)
# ---------------------------------------------------------------------------


def _gate() -> Any:
    """Return the DB-write gate as a context manager, or a null context when it
    is not initialised (a direct unit-test call to a job function)."""
    return _DB_WRITE_GATE if _DB_WRITE_GATE is not None else contextlib.nullcontext()


def _is_pool_exhaustion(exc: BaseException) -> bool:
    """True when *exc* (or a cause in its chain) is a DB connection-pool
    exhaustion — either the raw ``ConnectionPoolError`` raised by a direct store
    call or the ``DatabaseConnectionError`` the persistence helper translates it
    into."""
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 10:
        if isinstance(current, (ConnectionPoolError, DatabaseConnectionError)):
            return True
        current = current.__cause__
        seen += 1
    return False


def _gated_db_write(fn: Callable[[], T], stop_event: threading.Event) -> T:
    """Run a backfill/draft DB write under the concurrency gate, with a brief
    retry on pool exhaustion (wait, don't fail the job). Non-pool errors
    propagate immediately."""
    for attempt in range(_DB_WRITE_RETRY_ATTEMPTS):
        try:
            with _gate():
                return fn()
        except Exception as exc:
            if not _is_pool_exhaustion(exc) or attempt == _DB_WRITE_RETRY_ATTEMPTS - 1:
                raise
            stop_event.wait(_DB_WRITE_RETRY_DELAYS[min(attempt, len(_DB_WRITE_RETRY_DELAYS) - 1)])
    # Unreachable: the loop returns or raises on the final attempt.
    raise RuntimeError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _dispatcher_loop(stop_event: threading.Event) -> None:
    global _DB_WRITE_GATE
    _DB_WRITE_GATE = threading.BoundedSemaphore(max(1, backfill_db_write_concurrency()))

    # Recover jobs a previous process left mid-flight (status='running').
    for reset, label in (
        (account_backfill_store.reset_running_to_pending, "backfill"),
        (draft_sync_store.reset_running_to_pending, "draft-sync"),
    ):
        try:
            reset()
        except Exception as exc:
            logger.warning(
                "Backfill worker: %s reset_running_to_pending failed (%s): %s",
                label, type(exc).__name__, exc, exc_info=exc,
            )

    max_concurrent = max(1, backfill_max_concurrent())
    poll = backfill_poll_interval_s()
    pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="backfill-job")
    backfill_in_flight: dict[str, Future] = {}
    draft_in_flight: dict[str, Future] = {}
    try:
        while not stop_event.is_set():
            try:
                _reap_retriable_failed()

                for aid in [a for a, fut in backfill_in_flight.items() if fut.done()]:
                    backfill_in_flight.pop(aid, None)
                for aid in [a for a, fut in draft_in_flight.items() if fut.done()]:
                    draft_in_flight.pop(aid, None)

                free = max_concurrent - len(backfill_in_flight) - len(draft_in_flight)
                # Draft-sync jobs first: they are quick (seconds), so claiming
                # them ahead of the hours-long backfill keeps drafts responsive.
                if free > 0:
                    for job in _claim_draft_jobs(free):
                        account_id = str(job.get("account_id") or "")
                        if not account_id or account_id in draft_in_flight:
                            continue
                        draft_in_flight[account_id] = pool.submit(_run_draft_sync_job, job, stop_event)
                        free -= 1
                        if free <= 0:
                            break
                if free > 0:
                    for job in _claim_backfill_jobs(free):
                        account_id = str(job.get("account_id") or "")
                        if not account_id or account_id in backfill_in_flight:
                            continue
                        backfill_in_flight[account_id] = pool.submit(_run_backfill_job, job, stop_event)
            except Exception as exc:
                # A failure in the loop body itself (pool.submit, bookkeeping)
                # must NOT kill the sole dispatcher thread — log and keep polling
                # so future jobs still get claimed.
                logger.error(
                    "Backfill dispatcher loop iteration failed (%s): %s",
                    type(exc).__name__, exc, exc_info=exc,
                )
            stop_event.wait(poll)
    finally:
        # Abandon in-flight waves — their checkpoint resumes them next start.
        pool.shutdown(wait=False)


def _reap_retriable_failed() -> None:
    """Revive failed backfill + draft-sync jobs (attempts < max, past the
    cool-off) back to pending so the dispatcher re-claims and resumes them.
    Best-effort — a reaper failure must not stall the poll."""
    max_attempts = backfill_max_attempts()
    for store, label in (
        (account_backfill_store, "backfill"),
        (draft_sync_store, "draft-sync"),
    ):
        try:
            revived = store.reset_retriable_failed_to_pending(max_attempts, _FAILED_RETRY_BACKOFF_S)
            if revived:
                logger.info("Backfill worker: revived %d retriable failed %s job(s).", revived, label)
        except Exception as exc:
            logger.warning(
                "Backfill worker: %s reaper failed (%s): %s",
                label, type(exc).__name__, exc, exc_info=exc,
            )


def _claim_backfill_jobs(limit: int) -> list[dict]:
    try:
        return account_backfill_store.claim_next_batch(limit)
    except Exception as exc:
        logger.warning(
            "Backfill worker: claim_next_batch failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
        return []


def _claim_draft_jobs(limit: int) -> list[dict]:
    try:
        return draft_sync_store.claim_next_batch(limit)
    except Exception as exc:
        logger.warning(
            "Backfill worker: draft claim_next_batch failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
        return []


def _mark_failed(account_id: str, error: str) -> None:
    try:
        with _gate():
            account_backfill_store.mark_failed(account_id, error)
    except Exception as exc:
        logger.warning(
            "Backfill worker: mark_failed for %s failed (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )


def _mark_draft_failed(account_id: str, error: str) -> None:
    try:
        with _gate():
            draft_sync_store.mark_failed(account_id, error)
    except Exception as exc:
        logger.warning(
            "Backfill worker: draft mark_failed for %s failed (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )


# ---------------------------------------------------------------------------
# Per-account job
# ---------------------------------------------------------------------------


# Worker-local copies of the emails_service auth helpers. A flat module under
# ``api/services/`` must not import another package's private submodule
# (``emails_service._comunes``) per the facade discipline — ``drafts_service`` /
# ``attachments_service`` keep their own copies for the same reason.
def _build_auth_context(
    accounts: list[dict[str, Any]],
    mailbox_id: str,
) -> tuple[
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    dict[str, tuple[str, str, str]],
]:
    """Build auth_payloads and label_lookup for accounts."""
    auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    label_lookup: dict[str, tuple[str, str, str]] = {}
    credentials_cache: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        provider = str(account.get("provider") or "").lower()
        if not account_id or not provider:
            continue
        if provider not in credentials_cache:
            credentials_cache[provider] = load_wrapped_app_credentials(provider)
        account_label = f"{mailbox_id}__{account_id}"
        auth_payloads[account_label] = (
            credentials_cache[provider],
            load_wrapped_account_tokens(mailbox_id, account_id, provider),
        )
        label_lookup[account_label] = (mailbox_id, account_id, provider)
    return auth_payloads, label_lookup


def _persist_refreshed_tokens(
    updated_tokens: dict[str, dict[str, Any]],
    label_lookup: dict[str, tuple[str, str, str]],
    *,
    fallback: type[ApiError],
) -> None:
    for account_label, token_payload in updated_tokens.items():
        ids = label_lookup.get(account_label)
        if not ids:
            continue
        mailbox_id, account_id, provider = ids
        payload = dict(token_payload or {})
        payload["access_token"] = unwrap_secret(payload.get("access_token"))
        payload["refresh_token"] = unwrap_secret(payload.get("refresh_token"))
        try:
            account_store.upsert_tokens(mailbox_id, account_id, provider, payload)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected worker token refresh persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise fallback("Failed to persist refreshed tokens during background worker job.") from exc


def _authenticate_job_manager(
    record: dict[str, Any],
    mailbox_id: str,
    account_id: str,
    account_label: str,
    *,
    fallback: type[ApiError],
) -> Any | None:
    """Build + silently authenticate a single-account manager for a worker job.

    Returns the authenticated manager, or ``None`` when silent auth failed (the
    caller marks the job failed). Shared by the backfill and draft-sync jobs.
    ``authenticate_all_silent`` does NOT raise on auth failure — it records the
    per-account error, so detect it via ``get_last_errors``, not try/except."""
    auth_payloads, label_lookup = _build_auth_context([record], mailbox_id)
    manager = build_manager_for_accounts([record])
    refreshed = manager.authenticate_all_silent(auth_payloads)
    auth_errors = manager.get_last_errors()
    if account_label in auth_errors:
        logger.warning(
            "Worker job %s: silent auth failed; marking failed.",
            account_id, exc_info=auth_errors.get(account_label),
        )
        return None
    if refreshed:
        _persist_refreshed_tokens(refreshed, label_lookup, fallback=fallback)
    return manager


def _run_backfill_job(job: dict, stop_event: threading.Event) -> None:
    """Process one account's backfill: authenticate, capture the anchor once,
    then paginate rate-paced waves until the target or the mailbox is
    exhausted. Every failure path marks the job failed (or leaves it running
    to resume) and NEVER lets an exception escape to tumble the worker."""
    account_id = str(job.get("account_id") or "")
    mailbox_id = str(job.get("mailbox_id") or "")
    provider = str(job.get("provider") or "")
    target_total = int(job.get("target_total") or 0)
    fetched_count = int(job.get("fetched_count") or 0)
    page_cursor = job.get("page_cursor")
    initial_sync_cursor = job.get("initial_sync_cursor")
    account_label = f"{mailbox_id}__{account_id}"

    try:
        record = account_store.get(mailbox_id, account_id)
        if record is None:
            # Account deleted (the FK CASCADE already removed the job row).
            logger.info("Backfill job %s skipped: account no longer exists.", account_id)
            return

        manager = _authenticate_job_manager(
            record, mailbox_id, account_id, account_label, fallback=BackfillJobError,
        )
        if manager is None:
            _mark_failed(account_id, "auth")
            return

        # Capture the incremental anchor ONCE, before the first wave.
        if not initial_sync_cursor:
            anchor = manager.capture_backfill_anchor(account_label)
            _gated_db_write(lambda: account_backfill_store.set_anchor(account_id, anchor), stop_event)
            initial_sync_cursor = anchor

        page_size_cap = _GMAIL_PAGE_SIZE if provider == "gmail" else _OUTLOOK_PAGE_SIZE

        while not stop_event.is_set():
            remaining = target_total - fetched_count
            if remaining <= 0:
                break
            page_size = min(page_size_cap, remaining)
            current_cursor = page_cursor
            wave_start = time.monotonic()
            try:
                page = retry_with_backoff(
                    lambda: manager.fetch_backfill_page(account_label, current_cursor, page_size),
                    attempts=_WAVE_RETRY_ATTEMPTS,
                    delays=_WAVE_RETRY_DELAYS,
                    is_retryable=lambda exc: isinstance(exc, EmailExternalAPIError),
                    retry_after_extractor=_wave_retry_after,
                    sleep=stop_event.wait,
                )
            except Exception as exc:
                logger.warning(
                    "Backfill job %s: wave fetch failed after retries (%s): %s",
                    account_id, type(exc).__name__, exc, exc_info=exc,
                )
                _mark_failed(account_id, "wave_fetch_failed")
                return

            upserts = page.upserts
            if len(upserts) > remaining:
                upserts = upserts[:remaining]
            _gated_db_write(
                lambda: persist_email_metadata_batch(account_id, upserts, fallback=BackfillJobError),
                stop_event,
            )
            fetched_count += len(upserts)
            page_cursor = page.next_cursor
            _gated_db_write(
                lambda: account_backfill_store.update_progress(account_id, fetched_count, page_cursor),
                stop_event,
            )

            if page.next_cursor is None or fetched_count >= target_total:
                break
            _pace_after_wave(provider, len(upserts), time.monotonic() - wave_start, stop_event)
        else:
            # while-condition went false without a break: shutdown requested
            # mid-backfill. Leave the job 'running'; reset_running_to_pending
            # recovers it on the next start and it resumes from its checkpoint.
            logger.info(
                "Backfill job %s paused on shutdown at %d/%d; will resume.",
                account_id, fetched_count, target_total,
            )
            return

        # Reached only on natural completion (exhausted / target reached).
        # Order matters: write the incremental cursor FIRST so the next sync is
        # incremental, THEN mark completed so the sync guard stops excluding it.
        _gated_db_write(
            lambda: update_sync_cursor(mailbox_id, account_id, initial_sync_cursor or "", fallback=BackfillJobError),
            stop_event,
        )
        _gated_db_write(lambda: account_backfill_store.mark_completed(account_id), stop_event)
        logger.info("Backfill job %s completed: %d emails.", account_id, fetched_count)
    except Exception as exc:
        logger.warning(
            "Backfill job %s failed unexpectedly (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )
        _mark_failed(account_id, "unexpected")


def _run_draft_sync_job(job: dict, stop_event: threading.Event) -> None:
    """Process one account's draft sync: authenticate, fetch the latest drafts
    from the provider, and atomically replace the local rows. Quick and
    single-shot (no pagination checkpoint). Never lets an exception escape."""
    account_id = str(job.get("account_id") or "")
    mailbox_id = str(job.get("mailbox_id") or "")
    account_label = f"{mailbox_id}__{account_id}"

    try:
        record = account_store.get(mailbox_id, account_id)
        if record is None:
            logger.info("Draft sync job %s skipped: account no longer exists.", account_id)
            return

        manager = _authenticate_job_manager(
            record, mailbox_id, account_id, account_label, fallback=DraftSyncError,
        )
        if manager is None:
            _mark_draft_failed(account_id, "auth")
            return

        fetch_results = manager.fetch_all_drafts()
        fetch_errors = manager.get_last_errors()
        if account_label in fetch_errors:
            logger.warning(
                "Draft sync job %s: draft fetch failed; marking failed.",
                account_id, exc_info=fetch_errors.get(account_label),
            )
            _mark_draft_failed(account_id, "fetch")
            return

        rows = build_draft_rows(fetch_results.get(account_label, []))
        _gated_db_write(lambda: draft_store.replace_all_for_account(account_id, rows), stop_event)
        _gated_db_write(lambda: draft_sync_store.mark_completed(account_id), stop_event)
        logger.info("Draft sync job %s completed: %d drafts.", account_id, len(rows))
    except Exception as exc:
        logger.warning(
            "Draft sync job %s failed unexpectedly (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )
        _mark_draft_failed(account_id, "unexpected")


def _wave_retry_after(exc: Exception) -> float | None:
    """Best-effort Retry-After (seconds) for a wave retry. Outlook's transport
    already honours Retry-After INTERNALLY within a single wave call, so a
    surfaced ``EmailExternalAPIError`` rarely carries one; when a provider error
    does put a numeric ``retry_after`` in its ``detail`` we honour it, otherwise
    fall back to the fixed backoff. Gmail returns ``None`` here."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        value = detail.get("retry_after")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _pace_after_wave(
    provider: str, count: int, elapsed: float, stop_event: threading.Event,
) -> None:
    """Sleep between waves to keep the provider request rate under budget.

    Gmail (1 costly ``messages.get`` per message) paces to a target
    gets/minute; Outlook (metadata inline in the list) uses a small fixed
    per-page delay. The sleep is interruptible via ``stop_event.wait``.
    """
    if provider == "gmail":
        rate = backfill_gmail_gets_per_minute()
        if rate <= 0 or count <= 0:
            return
        min_wave_seconds = count / rate * 60.0
        sleep_for = min_wave_seconds - elapsed
        if sleep_for > 0:
            stop_event.wait(sleep_for)
    else:
        delay_s = backfill_outlook_page_delay_ms() / 1000.0
        if delay_s > 0:
            stop_event.wait(delay_s)
