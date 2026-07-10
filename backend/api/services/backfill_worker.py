"""In-process background worker for the initial mass backfill.

A single daemon dispatcher thread polls ``account_backfill_jobs`` for pending
jobs and runs each account through a bounded ``ThreadPoolExecutor``. Each job
paginates the provider in rate-paced, resumable waves, persisting each wave and
checkpointing progress so a process restart resumes from where it left off.

No Kafka/Celery/Redis — worker in-process + PostgreSQL (single uvicorn worker,
MVP), same profile as ``api/rate_limit.py``. Started/stopped from the app
lifespan; failures there are best-effort and never abort app startup.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import ApiError, BackfillJobError
from api.services.backfill_config import (
    backfill_gmail_gets_per_minute,
    backfill_max_concurrent,
    backfill_outlook_page_delay_ms,
    backfill_poll_interval_s,
    is_backfill_worker_enabled,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    persist_email_metadata_batch,
    translate_database_error,
    unwrap_secret,
    update_sync_cursor,
)
from core.email import EmailExternalAPIError, retry_with_backoff
from database import DatabaseError, account_backfill_store, account_store


# Wave-level retry (on top of the provider clients' own inner batch/transport
# retries): honours Retry-After for Outlook via the transport; for Gmail it is
# the fixed-backoff catch-all around the (non-retrying) messages.list call.
_WAVE_RETRY_ATTEMPTS = 3
_WAVE_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)

# Provider page-size caps (Gmail messages.list max 500; Graph $top max 1000).
_GMAIL_PAGE_SIZE = 500
_OUTLOOK_PAGE_SIZE = 1000

# On shutdown the dispatcher stops claiming and abandons in-flight waves (their
# checkpoint resumes them next start) — it does NOT block on hours-long jobs.
_DISPATCHER_JOIN_TIMEOUT_S = 10.0


_worker_lock = threading.Lock()
_dispatcher_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None


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
# Dispatcher
# ---------------------------------------------------------------------------


def _dispatcher_loop(stop_event: threading.Event) -> None:
    # Recover jobs a previous process left mid-flight (status='running').
    try:
        account_backfill_store.reset_running_to_pending()
    except Exception as exc:
        logger.warning(
            "Backfill worker: reset_running_to_pending failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )

    max_concurrent = max(1, backfill_max_concurrent())
    poll = backfill_poll_interval_s()
    pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="backfill-job")
    in_flight: dict[str, Future] = {}
    try:
        while not stop_event.is_set():
            try:
                for aid in [a for a, fut in in_flight.items() if fut.done()]:
                    in_flight.pop(aid, None)
                free = max_concurrent - len(in_flight)
                if free > 0:
                    for job in _claim_jobs(free):
                        account_id = str(job.get("account_id") or "")
                        if not account_id or account_id in in_flight:
                            continue
                        in_flight[account_id] = pool.submit(_run_backfill_job, job, stop_event)
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


def _claim_jobs(limit: int) -> list[dict]:
    try:
        return account_backfill_store.claim_next_batch(limit)
    except Exception as exc:
        logger.warning(
            "Backfill worker: claim_next_batch failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )
        return []


def _mark_failed(account_id: str, error: str) -> None:
    try:
        account_backfill_store.mark_failed(account_id, error)
    except Exception as exc:
        logger.warning(
            "Backfill worker: mark_failed for %s failed (%s): %s",
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
                "Unexpected backfill token refresh persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise fallback("Failed to persist refreshed tokens during backfill.") from exc


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

        auth_payloads, label_lookup = _build_auth_context([record], mailbox_id)
        manager = build_manager_for_accounts([record])
        refreshed = manager.authenticate_all_silent(auth_payloads)
        # authenticate_all_silent does NOT raise on auth failure — it records
        # the per-account error. Detect it that way, not via try/except.
        auth_errors = manager.get_last_errors()
        if account_label in auth_errors:
            logger.warning(
                "Backfill job %s: silent auth failed; marking failed.",
                account_id, exc_info=auth_errors.get(account_label),
            )
            _mark_failed(account_id, "auth")
            return
        if refreshed:
            _persist_refreshed_tokens(refreshed, label_lookup, fallback=BackfillJobError)

        # Capture the incremental anchor ONCE, before the first wave.
        if not initial_sync_cursor:
            anchor = manager.capture_backfill_anchor(account_label)
            account_backfill_store.set_anchor(account_id, anchor)
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
            persist_email_metadata_batch(account_id, upserts, fallback=BackfillJobError)
            fetched_count += len(upserts)
            page_cursor = page.next_cursor
            account_backfill_store.update_progress(account_id, fetched_count, page_cursor)

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
        update_sync_cursor(mailbox_id, account_id, initial_sync_cursor or "", fallback=BackfillJobError)
        account_backfill_store.mark_completed(account_id)
        logger.info("Backfill job %s completed: %d emails.", account_id, fetched_count)
    except Exception as exc:
        logger.warning(
            "Backfill job %s failed unexpectedly (%s): %s",
            account_id, type(exc).__name__, exc, exc_info=exc,
        )
        _mark_failed(account_id, "unexpected")


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
