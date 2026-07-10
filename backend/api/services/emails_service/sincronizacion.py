"""Sincronizacion de metadatos de correo, reconciliacion de fantasmas y prefetch/purga de contenido."""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailFetchError,
)
from core.email import (
    CoreError,
    EmailManager,
    SyncResult,
)
from api.schemas.email import (
    AccountSyncDetail,
    SyncResultOut,
)
from api.services.services_helpers import (
    build_account_sync_failures,
    build_manager_for_accounts,
    delete_email_metadata_batch,
    ensure_mailbox_access,
    list_unread_recent_uncached,
    load_suspect_message_ids,
    load_sync_cursors,
    persist_email_metadata_batch,
    purge_expired_email_content,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
    update_email_metadata_labels_batch,
    update_sync_cursor,
)
from database import (
    account_backfill_store,
    account_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens
from .contenido import _fetch_and_persist_email_content


# Sync-time content prefetch: at most the 50 most-recent unread (<=48h) inbox
# messages per account per sync (D6). The TTL (30 days) and the recency window
# (48 hours) live in the SQL strings (``PURGE_EXPIRED_FOR_ACCOUNTS`` /
# ``LIST_UNREAD_RECENT_UNCACHED``) — the only value the Python passes is this
# cap. The target box is ``ALL_MAIL`` (inbox), fixed inside the query.
_PREFETCH_LIMIT = 50


def _reconcile_ghost_emails(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    sync_result: SyncResult,
) -> tuple[int, list[str]]:
    """Best-effort: verify DB emails still exist at provider after bootstrap.
    Returns (deleted_count, ghost_ids). Skips on any error."""
    # Push the set-difference into SQL: only the stored ids absent from the
    # bootstrap set come back, instead of loading every stored id to diff in
    # Python (M8 — bounded memory on large full-syncs).
    bootstrap_ids = [m.provider_message_id for m in sync_result.upserts]
    try:
        suspect_ids = load_suspect_message_ids(account_id, bootstrap_ids)
    except Exception as exc:
        logger.warning(
            "Reconciliation skipped for %s: failed to load suspect IDs (%s): %s",
            account_id, type(exc).__name__, exc,
            exc_info=exc,
        )
        return 0, []

    if not suspect_ids:
        return 0, []

    try:
        still_exist = set(manager.verify_message_existence(account_label, suspect_ids))
    except Exception as exc:
        logger.warning(
            "Reconciliation skipped for %s: verification failed (%s): %s",
            account_id, type(exc).__name__, exc,
            exc_info=exc,
        )
        return 0, []

    ghost_ids = [mid for mid in suspect_ids if mid not in still_exist]
    if not ghost_ids:
        return 0, []

    try:
        deleted = delete_email_metadata_batch(account_id, ghost_ids)
    except Exception as exc:
        logger.warning(
            "Reconciliation skipped for %s: failed to delete ghosts (%s): %s",
            account_id, type(exc).__name__, exc,
            exc_info=exc,
        )
        return 0, []
    logger.info("Reconciliation for %s: %d suspect, %d ghosts deleted.", account_id, len(suspect_ids), deleted)
    return deleted, ghost_ids


def sync_email_metadata(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> SyncResultOut:
    """Fetch and persist email metadata for a mailbox, or a single account if specified.

    When ``background_tasks`` is provided (the HTTP router injects it), a
    post-response background task purges expired cached bodies and prefetches
    the content of recent unread inbox mail for the synced accounts so opening
    those messages is instant. Direct callers (service-level tests, scripts)
    may omit it — the prefetch/purge is a best-effort optimisation and is
    simply skipped when absent, leaving the sync contract unchanged.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during metadata sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailFetchError(
                "Failed to look up account for metadata sync."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during metadata sync."
            )
        accounts = [account]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning("Unexpected account listing error during sync (%s): %s", type(exc).__name__, exc)
            raise EmailFetchError("Failed to list accounts for metadata sync.") from exc

    # Guard: exclude accounts under an ACTIVE backfill (pending/running) from
    # the normal sync. During the initial mass backfill ``sync_cursor`` is NULL,
    # so a sync-metadata would take the bootstrap path (fetch_all_email_metadata
    # passes sync_cursor=None -> _bootstrap_email_metadata(500)) and double-write
    # alongside the background worker. The worker owns the initial load. This
    # guard is INDEPENDENT of BACKFILL_WORKER_ENABLED (a job that exists is
    # honoured regardless) — the no-stranding when the worker is off is solved
    # at enqueue time (§4.4/§4.10), which also keeps this guard testable without
    # launching the background thread. Excluded accounts keep surfacing as
    # "loading" via GET /backfill-status.
    try:
        active_backfill_ids = set(account_backfill_store.list_active_account_ids(mailbox_id))
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected active-backfill lookup error during sync (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailFetchError("Failed to load active backfill jobs for metadata sync.") from exc

    if active_backfill_ids:
        accounts = [a for a in accounts if str(a.get("account_id")) not in active_backfill_ids]
        # Single account in backfill, or every account of a unified mailbox in
        # backfill: return a neutral 200 (no bootstrap) — the accounts are still
        # "loading" via the status endpoint.
        if not accounts:
            return SyncResultOut(total_synced=0, accounts=[])

    try:
        auth_payloads, label_lookup = _build_auth_context(accounts, mailbox_id)

        manager = build_manager_for_accounts(accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailFetchError)
        # Collect — but do NOT yet raise on — per-account silent-auth failures.
        # A single expired/revoked token must not abort the whole unified sync:
        # the healthy accounts still sync and the dead one is reported at the
        # end, instead of tumbling the batch at the first stumble
        # (sincronizacion.md §7). Snapshot the errors now because
        # ``fetch_all_email_metadata`` below resets the manager's error map, and
        # the refresh error captured here ("token revoked …") is more
        # informative than the generic "not authenticated" the fetch raises.
        auth_errors = dict(manager.get_last_errors())

        sync_cursors = load_sync_cursors(label_lookup, fallback=EmailFetchError)

        try:
            results = manager.fetch_all_email_metadata(sync_cursors)
        except CoreError as exc:
            raise translate_core_error(exc, fallback=EmailFetchError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected failure fetching all email metadata (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailFetchError(
                "Unexpected failure fetching all email metadata."
            ) from exc

        # Merge the per-account fetch failures with the auth failures collected
        # above; the auth-phase error wins for an account that failed both (it
        # names the real cause). Evaluated AFTER the healthy accounts persist —
        # see the deferred raise past the loop.
        sync_errors = {**manager.get_last_errors(), **auth_errors}

        account_details: list[AccountSyncDetail] = []
        total_synced = 0
        # Content prefetch/purge targets, collected only for the accounts
        # effectively synced (those that did NOT ``continue`` on empty ids).
        # ``label`` IS the account_label (``_build_auth_context`` keys
        # ``label_lookup`` by it), so it is passed straight to the prefetch
        # — do NOT reconstruct ``f"{mailbox_id}__{aid}"``.
        prefetch_targets: list[tuple[str, str]] = []
        synced_account_ids: list[str] = []

        # Accounts whose backfill has COMPLETED: skip ghost reconciliation for
        # them. If a backfilled account's incremental cursor ever expires (rare
        # in 2-6h) the fetch falls back to bootstrap(500); with up to 100k local
        # rows, reconciliation would treat ~99,500 as suspects and mass-delete
        # the history. Skipping it re-anchors the cursor without purging the
        # history. Computed once (the same list_by_mailbox the status endpoint
        # uses) and consulted in memory (§4.6.2).
        try:
            backfill_rows = account_backfill_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected completed-backfill lookup error during sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailFetchError(
                "Failed to load completed backfill jobs for ghost reconciliation gating."
            ) from exc
        completed_backfill_ids = {
            str(row["account_id"])
            for row in backfill_rows
            if row.get("status") == "completed"
        }

        for label, sync_result in results.items():
            ids = label_lookup.get(label)
            if not ids:
                continue
            if label in sync_errors:
                # This account failed silent auth or the metadata fetch; never
                # persist a (possibly partial) result for it nor target it for
                # prefetch — it is reported after the loop, either in the 200
                # ``failed_accounts`` (partial success) or via the deferred
                # raise (total failure), once the healthy accounts have landed
                # (sincronizacion.md §7).
                continue
            mid, aid, provider = ids
            prefetch_targets.append((label, aid))
            synced_account_ids.append(aid)

            upserted = persist_email_metadata_batch(aid, sync_result.upserts, fallback=EmailFetchError)
            deleted = delete_email_metadata_batch(aid, sync_result.deletes, fallback=EmailFetchError)
            label_updated = update_email_metadata_labels_batch(aid, sync_result.label_updates, fallback=EmailFetchError)
            update_sync_cursor(mid, aid, sync_result.new_cursor, fallback=EmailFetchError)

            reconciled, ghost_ids = 0, []
            if sync_result.is_full_sync and aid not in completed_backfill_ids:
                reconciled, ghost_ids = _reconcile_ghost_emails(manager, label, aid, sync_result)

            count = upserted + deleted + label_updated + reconciled
            total_synced += count

            events: list[str] = []
            for meta in sync_result.upserts:
                events.append(
                    f"UPSERT  | id={meta.provider_message_id} | box={meta.box} | subject={meta.subject!r}"
                )
            for msg_id in sync_result.deletes:
                events.append(f"DELETE  | id={msg_id}")
            for lu in sync_result.label_updates:
                events.append(
                    f"LABEL   | id={lu.provider_message_id} | box={lu.box} | is_read={lu.is_read}"
                )
            for ghost_id in ghost_ids:
                events.append(f"GHOST   | id={ghost_id}")

            if events:
                logger.info("Sync events for account %s (%s) [%d events]:", aid, provider, len(events))
                for event in events:
                    logger.info("  %s", event)

            account_details.append(AccountSyncDetail(
                account_id=aid,
                provider=provider,
                emails_synced=count,
                sync_cursor=sync_result.new_cursor,
            ))

        # Every healthy account has synced and persisted. Turn the per-account
        # failures into ``failed_accounts`` rows WITHOUT raising, so a partial
        # success can report them in the 200 response.
        failed_accounts = build_account_sync_failures(sync_errors, label_lookup)

        # Raise ONLY on a genuine total failure: there were errors AND not a
        # single account synced. That still covers the single-account view whose
        # one account is broken (409 AccountNotConnected / the typed non-auth
        # error, unchanged) and the unified mailbox where EVERY account failed.
        # With a partial success (>=1 account synced) we deliberately do NOT
        # raise: the healthy mail is already in the local copy and the dead
        # account(s) travel in ``failed_accounts`` (Option A), so the unified
        # view updates and merely surfaces a non-blocking "reconnect" notice
        # instead of the blocking "could not update" error
        # (sincronizacion.md §7, refrescar-y-estado-sincronizacion.md §5.1). The
        # ``sync_errors and`` guard keeps the empty-mailbox case (0 accounts, no
        # errors) a plain 200 — although ``raise_on_silent_auth_errors`` is
        # itself a no-op on an empty map, the guard reads clearer at the site.
        if sync_errors and not synced_account_ids:
            raise_on_silent_auth_errors(sync_errors, fallback=EmailFetchError)

        # After responding (D2/D3), purge expired cached bodies and prefetch
        # recent-unread inbox content for the synced accounts, reusing the
        # already-authenticated ``manager``. Best-effort, off the response
        # path — skipped when no ``BackgroundTasks`` was injected.
        if background_tasks is not None:
            background_tasks.add_task(
                _run_content_prefetch_and_purge,
                manager,
                prefetch_targets,
                synced_account_ids,
            )

        return SyncResultOut(
            total_synced=total_synced,
            accounts=account_details,
            failed_accounts=failed_accounts,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning("Unexpected sync error (%s): %s", type(exc).__name__, exc)
        raise EmailFetchError("Failed to sync email metadata.") from exc


def _run_content_prefetch_and_purge(
    manager: EmailManager,
    targets: list[tuple[str, str]],
    account_ids: list[str],
) -> None:
    """Post-sync background job: purge expired cached bodies, then prefetch.

    Runs after the sync response is sent (FastAPI ``BackgroundTasks``).
    Entirely best-effort — every failure is logged and swallowed so it can
    never affect the already-sent response nor abort the remaining work:

    1. Purge cached bodies idle for 30+ days for the synced accounts (one
       indexed DELETE, frees space before the prefetch refills it).
    2. Prefetch, sequentially and message-by-message (D2 — sidesteps both
       providers' per-user / per-mailbox concurrency 429s), the body +
       attachments of up to ``_PREFETCH_LIMIT`` recent-unread inbox messages
       per account that are not yet cached. A failure on one message does
       not abort the rest.

    The ``manager`` is the one authenticated during the sync; the prefetch
    runs seconds later so the tokens are still fresh — it does NOT
    re-authenticate nor re-persist tokens (the next sync handles rotation).
    """
    purge_expired_email_content(account_ids)
    for account_label, account_id in targets:
        try:
            pmids = list_unread_recent_uncached(
                account_id, _PREFETCH_LIMIT, fallback=EmailFetchError,
            )
        except Exception as exc:
            logger.warning(
                "Content prefetch target selection failed for account '%s' (%s): %s",
                account_id, type(exc).__name__, exc,
                exc_info=exc,
            )
            continue
        for pmid in pmids:
            try:
                _fetch_and_persist_email_content(
                    manager, account_label, account_id, pmid,
                )
            except Exception as exc:
                logger.warning(
                    "Content prefetch failed for message '%s' of account '%s' (%s): %s",
                    pmid, account_id, type(exc).__name__, exc,
                    exc_info=exc,
                )
                continue
