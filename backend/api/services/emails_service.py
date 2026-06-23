"""
Service layer for email operations.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)

# Sync-time content prefetch: at most the 50 most-recent unread (<=48h) inbox
# messages per account per sync (D6). The TTL (30 days) and the recency window
# (48 hours) live in the SQL strings (``PURGE_EXPIRED_FOR_ACCOUNTS`` /
# ``LIST_UNREAD_RECENT_UNCACHED``) — the only value the Python passes is this
# cap. The target box is ``ALL_MAIL`` (inbox), fixed inside the query.
_PREFETCH_LIMIT = 50

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    ConversationFetchError,
    EmailContentFetchError,
    EmailFetchError,
    EmailListError,
    EmailNotFound,
    EmailNotInTrash,
    EmailReplyContextError,
    EmailSendError,
    FavoriteSyncError,
    FavoriteUpdateError,
    MoveToTrashError,
    ReadStatusUpdateError,
    SpamMoveError,
    SpamRestoreError,
    TrashOperationError,
    UnreadCountError,
)
from core.email import (
    ConversationMessage,
    CoreError,
    EmailManager,
    EmailMetadata,
    SyncResult,
    build_in_reply_to_and_references,
    build_quoted_body_html,
    build_reply_subject,
    compute_reply_recipients,
    sanitize_filename,
    validate_reply_threading_coherence,
)
from api.schemas.email import (
    AccountReadStatusDetail,
    AccountSpamDetail,
    AccountSyncDetail,
    AccountUnreadDetail,
    ConversationOut,
    EmailContentOut,
    EmailMetadataOut,
    EmailPageOut,
    EmailSendRequest,
    FavoriteSyncAccountDetail,
    FavoriteSyncResponse,
    FavoriteUpdateResponse,
    MoveToTrashRequest,
    MoveToTrashResult,
    ReadStatusRequest,
    ReadStatusResponse,
    ReplyContextOut,
    SpamRequest,
    SpamResponse,
    SyncResultOut,
    TrashActionRequest,
    TrashActionResult,
    UnreadCountOut,
)
from api.schemas.attachment import AttachmentMetadataOut
from api.services.services_helpers import (
    build_manager_for_accounts,
    delete_email_metadata_batch,
    ensure_mailbox_access,
    get_email_content,
    get_trash_emails_by_ids,
    list_unread_recent_uncached,
    load_suspect_message_ids,
    load_sync_cursors,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    mark_as_deleted_batch,
    move_to_trash_batch,
    parse_search_query,
    persist_email_content,
    persist_email_metadata_batch,
    purge_expired_email_content,
    raise_on_silent_auth_errors,
    recompute_has_attachments,
    restore_from_trash_batch,
    restore_from_trash_discovered_batch,
    row_to_email_metadata_out,
    sanitize_email_html,
    sanitize_outbound_html,
    touch_email_content_last_accessed,
    translate_core_error,
    translate_database_error,
    unwrap_secret,
    update_email_metadata_labels_batch,
    update_email_read_status_batch,
    update_email_spam_status_batch,
    update_sync_cursor,
)
from database import (
    account_store,
    email_attachment_store,
    email_metadata_store,
    DatabaseError,
)


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
        )
        return 0, []
    logger.info("Reconciliation for %s: %d suspect, %d ghosts deleted.", account_id, len(suspect_ids), deleted)
    return deleted, ghost_ids


def _persist_refreshed_tokens(
    updated_tokens: dict[str, dict[str, Any]],
    label_lookup: dict[str, tuple[str, str, str]],
    *,
    fallback: type[ApiError],
) -> None:
    # ``fallback`` is a required keyword: every caller passes the ApiError
    # subclass matching its operation. A base ``ApiError`` default would emit
    # a code-less 500 if a future caller forgot it — a required arg fails
    # loudly at call time instead (every current call site is explicit).
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
            logger.warning("Unexpected token refresh persist error (%s): %s", type(exc).__name__, exc)
            raise fallback("Failed to persist refreshed tokens.") from exc


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

    try:
        auth_payloads, label_lookup = _build_auth_context(accounts, mailbox_id)

        manager = build_manager_for_accounts(accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailFetchError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailFetchError)

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

        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailFetchError)

        account_details: list[AccountSyncDetail] = []
        total_synced = 0
        # Content prefetch/purge targets, collected only for the accounts
        # effectively synced (those that did NOT ``continue`` on empty ids).
        # ``label`` IS the account_label (``_build_auth_context`` keys
        # ``label_lookup`` by it), so it is passed straight to the prefetch
        # — do NOT reconstruct ``f"{mailbox_id}__{aid}"``.
        prefetch_targets: list[tuple[str, str]] = []
        synced_account_ids: list[str] = []

        for label, sync_result in results.items():
            ids = label_lookup.get(label)
            if not ids:
                continue
            mid, aid, provider = ids
            prefetch_targets.append((label, aid))
            synced_account_ids.append(aid)

            upserted = persist_email_metadata_batch(aid, sync_result.upserts, fallback=EmailFetchError)
            deleted = delete_email_metadata_batch(aid, sync_result.deletes, fallback=EmailFetchError)
            label_updated = update_email_metadata_labels_batch(aid, sync_result.label_updates, fallback=EmailFetchError)
            update_sync_cursor(mid, aid, sync_result.new_cursor, fallback=EmailFetchError)

            reconciled, ghost_ids = 0, []
            if sync_result.is_full_sync:
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

        return SyncResultOut(total_synced=total_synced, accounts=account_details)
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
                )
                continue


def send_email(mailbox_id: str, payload: EmailSendRequest, user_id: str) -> dict[str, str]:
    ensure_mailbox_access(mailbox_id, user_id)
    # Sanitise the rich-text HTML body at the trust boundary before it
    # reaches the provider (Gmail multipart/alternative, Outlook HTML).
    # Fail-soft (never raises). NOTE: ``sanitize_outbound_html`` (outbound,
    # strict allowlist) is distinct from ``sanitize_email_html`` (the
    # inbound viewer pipeline) — they are NOT interchangeable.
    sanitized_body = sanitize_outbound_html(payload.body)
    try:
        account = account_store.get(mailbox_id, payload.account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error (%s): %s", type(exc).__name__, exc)
        raise EmailSendError("Failed to look up account for email send.") from exc
    if account is None:
        raise AccountNotFound(f"Account '{payload.account_id}' not found.")

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{payload.account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailSendError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailSendError)

        try:
            sent_metadata = manager.send_email_from_account(
                account_label=account_label,
                subject=payload.subject,
                body=sanitized_body,
                recipients=payload.recipients,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=EmailSendError,
                context={"account_id": payload.account_id, "account_label": account_label},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider send_email_from_account (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailSendError(
                "Unexpected provider failure while sending email from account."
            ) from exc

        # Best-effort: email already sent, don't fail the response on metadata persist failure
        try:
            persist_email_metadata_batch(payload.account_id, [sent_metadata], fallback=EmailSendError)
        except Exception as exc:
            logger.warning(
                "Email sent but metadata persistence failed for account '%s' (%s): %s",
                payload.account_id, type(exc).__name__, exc,
            )

        return {"status": "sent"}
    except ApiError:
        raise
    except Exception as exc:
        logger.warning("Unexpected send error (%s): %s", type(exc).__name__, exc)
        raise EmailSendError("Failed to send email.") from exc


def manage_trash(mailbox_id: str, payload: TrashActionRequest, user_id: str) -> TrashActionResult:
    """Delete permanently or restore emails from trash."""
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account listing error during trash operation (%s): %s", type(exc).__name__, exc)
        raise TrashOperationError("Failed to list accounts for trash operation.") from exc

    account_ids_in_mailbox = {str(a.get("account_id") or "") for a in accounts}

    # Group message IDs by account_id
    msg_ids_by_account: dict[str, list[str]] = {}
    for item in payload.items:
        if item.account_id not in account_ids_in_mailbox:
            raise AccountNotFound(
                f"Account '{item.account_id}' not found in mailbox '{mailbox_id}'."
            )
        msg_ids_by_account.setdefault(item.account_id, []).append(item.provider_message_id)

    # Verify all emails are in TRASH and collect trash data
    trash_data_by_account: dict[str, dict[str, str | None]] = {}
    for account_id, msg_ids in msg_ids_by_account.items():
        trash_rows = get_trash_emails_by_ids(account_id, msg_ids, fallback=TrashOperationError)
        found_ids = {str(r["provider_message_id"]) for r in trash_rows}
        missing = [mid for mid in msg_ids if mid not in found_ids]
        if missing:
            raise EmailNotInTrash(
                f"Emails not in trash for account '{account_id}': {missing}.",
                {"account_id": account_id, "missing_ids": missing},
            )
        trash_data_by_account[account_id] = {
            str(r["provider_message_id"]): r.get("previous_box")
            for r in trash_rows
        }

    # Build auth context only for referenced accounts
    referenced_accounts = [a for a in accounts if str(a.get("account_id") or "") in msg_ids_by_account]

    try:
        auth_payloads, label_lookup = _build_auth_context(referenced_accounts, mailbox_id)
        manager = build_manager_for_accounts(referenced_accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=TrashOperationError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=TrashOperationError)

        total_affected = 0
        for account_id, msg_ids in msg_ids_by_account.items():
            account_label = f"{mailbox_id}__{account_id}"

            if payload.action == "delete":
                succeeded = manager.delete_messages(account_label, msg_ids)
                mark_as_deleted_batch(account_id, succeeded, fallback=TrashOperationError)
                total_affected += len(succeeded)
            else:  # restore
                trash_data = trash_data_by_account[account_id]
                provider_items = {mid: trash_data.get(mid) for mid in msg_ids}

                try:
                    id_mapping = manager.restore_from_trash(account_label, provider_items)
                except CoreError as exc:
                    raise translate_core_error(
                        exc, fallback=TrashOperationError,
                        context={"account_id": account_id, "account_label": account_label},
                    ) from exc
                except Exception as exc:
                    logger.warning(
                        "Unexpected error during provider restore_from_trash (%s): %s",
                        type(exc).__name__, exc,
                    )
                    raise TrashOperationError(
                        "Unexpected provider failure while restoring messages from trash."
                    ) from exc

                # Split: known previous_box vs NULL (needs discovery)
                known_rows: list[tuple] = []
                null_old_to_new: dict[str, str] = {}
                for old, new in id_mapping.items():
                    if trash_data.get(old):
                        known_rows.append((old, new, account_id))
                    else:
                        null_old_to_new[old] = new

                if known_rows:
                    restore_from_trash_batch(account_id, known_rows, fallback=TrashOperationError)

                if null_old_to_new:
                    new_ids = list(null_old_to_new.values())
                    try:
                        metadata_list = manager.fetch_messages_metadata(account_label, new_ids)
                    except CoreError as exc:
                        raise translate_core_error(
                            exc, fallback=TrashOperationError,
                            context={"account_id": account_id, "account_label": account_label},
                        ) from exc
                    except Exception as exc:
                        logger.warning(
                            "Unexpected error during provider fetch_messages_metadata for restore (%s): %s",
                            type(exc).__name__, exc,
                        )
                        raise TrashOperationError(
                            "Unexpected provider failure while fetching restored message metadata."
                        ) from exc
                    box_map = {m.provider_message_id: m.box for m in metadata_list}
                    discovered_rows = [
                        (old, new, account_id, box_map.get(new, "ALL_MAIL"))
                        for old, new in null_old_to_new.items()
                    ]
                    restore_from_trash_discovered_batch(account_id, discovered_rows, fallback=TrashOperationError)

                total_affected += len(id_mapping)

        return TrashActionResult(affected=total_affected)
    except ApiError:
        raise
    except CoreError as exc:
        raise translate_core_error(exc, fallback=TrashOperationError) from exc
    except Exception as exc:
        logger.warning("Unexpected trash operation error (%s): %s", type(exc).__name__, exc)
        raise TrashOperationError("Failed to manage trash operation.") from exc


def move_to_trash(mailbox_id: str, payload: MoveToTrashRequest, user_id: str) -> MoveToTrashResult:
    """Move emails to trash (provider-first, then update DB)."""
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account listing error during move-to-trash (%s): %s", type(exc).__name__, exc)
        raise MoveToTrashError("Failed to list accounts for move-to-trash operation.") from exc

    account_ids_in_mailbox = {str(a.get("account_id") or "") for a in accounts}

    items_by_account: dict[str, list[str]] = {}
    for item in payload.items:
        if item.account_id not in account_ids_in_mailbox:
            raise AccountNotFound(
                f"Account '{item.account_id}' not found in mailbox '{mailbox_id}'."
            )
        items_by_account.setdefault(item.account_id, []).append(item.provider_message_id)

    referenced_accounts = [a for a in accounts if str(a.get("account_id") or "") in items_by_account]

    try:
        auth_payloads, label_lookup = _build_auth_context(referenced_accounts, mailbox_id)
        manager = build_manager_for_accounts(referenced_accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=MoveToTrashError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=MoveToTrashError)

        total_affected = 0
        for account_id, msg_ids in items_by_account.items():
            account_label = f"{mailbox_id}__{account_id}"
            try:
                id_mapping = manager.move_to_trash(account_label, msg_ids)
            except CoreError as exc:
                raise translate_core_error(
                    exc, fallback=MoveToTrashError,
                    context={"account_id": account_id, "account_label": account_label},
                ) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected error during provider move_to_trash (%s): %s",
                    type(exc).__name__, exc,
                )
                raise MoveToTrashError(
                    "Unexpected provider failure while moving emails to trash."
                ) from exc
            if id_mapping:
                rows = [(old, new, account_id) for old, new in id_mapping.items()]
                affected = move_to_trash_batch(account_id, rows, fallback=MoveToTrashError)
                total_affected += affected

        return MoveToTrashResult(affected=total_affected)
    except ApiError:
        raise
    except CoreError as exc:
        raise translate_core_error(exc, fallback=MoveToTrashError) from exc
    except Exception as exc:
        logger.warning("Unexpected move-to-trash error (%s): %s", type(exc).__name__, exc)
        raise MoveToTrashError("Failed to move emails to trash.") from exc


def update_read_status(
    mailbox_id: str,
    payload: ReadStatusRequest,
    user_id: str,
) -> ReadStatusResponse:
    """Mark messages as read/unread across accounts in a mailbox."""
    ensure_mailbox_access(mailbox_id, user_id)

    items_by_account: dict[str, list[str]] = {}
    for item in payload.items:
        items_by_account.setdefault(item.account_id, []).append(item.provider_message_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise ReadStatusUpdateError(
            "Failed to list accounts for read status update."
        ) from exc

    account_map = {str(a["account_id"]): a for a in accounts}
    for aid in items_by_account:
        if aid not in account_map:
            raise AccountNotFound(
                f"Account '{aid}' not found in mailbox '{mailbox_id}' "
                "during read status update."
            )

    referenced_accounts = [account_map[aid] for aid in items_by_account]

    try:
        auth_payloads, label_lookup = _build_auth_context(
            referenced_accounts, mailbox_id,
        )
        manager = build_manager_for_accounts(referenced_accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=ReadStatusUpdateError)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=ReadStatusUpdateError,
        )

        account_details: list[AccountReadStatusDetail] = []
        total_updated = 0

        for aid, message_ids in items_by_account.items():
            account_label = f"{mailbox_id}__{aid}"

            try:
                updated_ids = manager.update_read_status(
                    account_label, message_ids, payload.is_read,
                )
            except CoreError as exc:
                raise translate_core_error(
                    exc, fallback=ReadStatusUpdateError,
                    context={"account_id": aid, "account_label": account_label},
                ) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected error during provider update_read_status (%s): %s",
                    type(exc).__name__, exc,
                )
                raise ReadStatusUpdateError(
                    "Unexpected provider failure while updating read status."
                ) from exc

            if updated_ids:
                update_email_read_status_batch(aid, updated_ids, payload.is_read, fallback=ReadStatusUpdateError)

            account_details.append(AccountReadStatusDetail(
                account_id=aid,
                updated=len(updated_ids),
            ))
            total_updated += len(updated_ids)

        return ReadStatusResponse(
            updated_count=total_updated,
            accounts=account_details,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected read status update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise ReadStatusUpdateError(
            "Failed to update email read status."
        ) from exc


def move_to_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str,
) -> SpamResponse:
    """Move emails to spam across accounts in a mailbox."""
    return _execute_spam_operation(
        mailbox_id, payload, user_id,
        manager_method="move_to_spam",
        target_box="SPAM",
        fallback_error=SpamMoveError,
        operation_label="spam move",
    )


def restore_from_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str,
) -> SpamResponse:
    """Restore emails from spam across accounts in a mailbox."""
    return _execute_spam_operation(
        mailbox_id, payload, user_id,
        manager_method="restore_from_spam",
        target_box="ALL_MAIL",
        fallback_error=SpamRestoreError,
        operation_label="spam restore",
    )


def _execute_spam_operation(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str,
    *,
    manager_method: str,
    target_box: str,
    fallback_error: type[ApiError],
    operation_label: str,
) -> SpamResponse:
    """Shared implementation for move-to-spam and restore-from-spam."""
    ensure_mailbox_access(mailbox_id, user_id)

    items_by_account: dict[str, list[str]] = {}
    for item in payload.items:
        items_by_account.setdefault(item.account_id, []).append(item.provider_message_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise fallback_error(f"Failed to list accounts during {operation_label}.") from exc

    account_map = {str(a["account_id"]): a for a in accounts}
    for aid in items_by_account:
        if aid not in account_map:
            raise AccountNotFound(
                f"Account '{aid}' not found in mailbox '{mailbox_id}' "
                f"during {operation_label}."
            )

    referenced_accounts = [account_map[aid] for aid in items_by_account]

    try:
        auth_payloads, label_lookup = _build_auth_context(
            referenced_accounts, mailbox_id,
        )
        manager = build_manager_for_accounts(referenced_accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=fallback_error)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=fallback_error,
        )

        account_details: list[AccountSpamDetail] = []
        total_moved = 0

        for aid, message_ids in items_by_account.items():
            account_label = f"{mailbox_id}__{aid}"

            try:
                results = getattr(manager, manager_method)(account_label, message_ids)
            except CoreError as exc:
                raise translate_core_error(
                    exc, fallback=fallback_error,
                    context={"account_id": aid, "account_label": account_label},
                ) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected error during provider %s (%s): %s",
                    manager_method, type(exc).__name__, exc,
                )
                raise fallback_error(
                    f"Unexpected provider failure during {operation_label}."
                ) from exc

            if results:
                update_email_spam_status_batch(aid, results, target_box, fallback=fallback_error)

            account_details.append(AccountSpamDetail(
                account_id=aid,
                moved=len(results),
            ))
            total_moved += len(results)

        return SpamResponse(
            moved_count=total_moved,
            accounts=account_details,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected %s error (%s): %s",
            operation_label, type(exc).__name__, exc,
        )
        raise fallback_error(f"Failed to execute {operation_label}.") from exc


def list_emails(
    mailbox_id: str,
    box: str,
    user_id: str,
    account_id: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    favorite: bool | None = None,
    group_by_thread: bool = False,
) -> EmailPageOut:
    """List a page of email metadata for a mailbox, with the exact total.

    Returns an :class:`EmailPageOut` envelope: the requested page of
    rows plus ``total`` — the count of the WHOLE filtered set (same
    ``box`` / ``q`` / ``favorite`` / accounts), used by the frontend to
    render numbered pagination. ``total`` reflects only the locally
    synced copy, never the provider's live mailbox size.

    When ``favorite=True``, the listing only returns favourite messages
    and TRASH / SPAM are excluded by default (matching the dedicated
    Favourites view documented in ``Ignore/Favoritos-Funcionalidad.md``).
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during email listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailListError(
                "Failed to look up account for email listing."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during email listing."
            )
        account_ids: list[str] = [account_id]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account listing error during email listing for mailbox '%s' (%s): %s",
                mailbox_id, type(exc).__name__, exc,
            )
            raise EmailListError(
                "Failed to load mailbox accounts for email listing."
            ) from exc
        account_ids = [str(a["account_id"]) for a in accounts]
        if not account_ids:
            return EmailPageOut(items=[], total=0, limit=limit, offset=offset)

    # ``parse_search_query`` is a pure string operation (no DB) computed
    # once and shared by BOTH calls below, so ``count_filtered`` counts
    # EXACTLY the set ``list_filtered`` lists (same box / tokens /
    # extra_filters / box_not_in / operator_clauses). Two separate try
    # blocks keep the failure messages unique per raise site (API
    # CLAUDE.md §7).
    parsed = parse_search_query(q)
    tokens = parsed.tokens
    operator_clauses = parsed.operator_clauses

    extra_filters: dict[str, Any] = {}
    box_arg: str | None = box
    box_not_in: list[str] | None = None
    if favorite is True:
        extra_filters["is_favorite"] = True
        # ``box=ALL_MAIL`` is the "everywhere except trash and spam"
        # anchor used by the dedicated FavoritesPage (see the comment
        # in ``frontend/src/features/emails/pages/FavoritesPage.tsx``).
        # For any other explicit box (SENT / SPAM / TRASH) we respect
        # the caller's choice — otherwise the SENT favourites view
        # would silently surface ALL_MAIL favourites too.
        if box == "ALL_MAIL":
            box_arg = None
            box_not_in = ["TRASH", "SPAM"]

    # ``in:`` overrides the box shown — it wins over the route's ``box``
    # and over the Favourites ``ALL_MAIL`` anchor (``is_favorite`` stays
    # in ``extra_filters``, so ``in:sent`` means "favourites in Sent").
    # Setting ``box_arg`` and clearing ``box_not_in`` keeps the
    # mutually-exclusive contract the repository relies on.
    if parsed.box_override is not None:
        box_arg = parsed.box_override
        box_not_in = None

    try:
        rows = email_metadata_store.list_filtered(
            account_ids, box_arg, tokens, limit, offset,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            group_by_thread=group_by_thread,
            operator_clauses=operator_clauses or None,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected email metadata listing error for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise EmailListError(
            "Failed to list email metadata for filtered listing."
        ) from exc

    try:
        total = email_metadata_store.count_filtered(
            account_ids, box_arg, tokens,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            group_by_thread=group_by_thread,
            operator_clauses=operator_clauses or None,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected email metadata count error for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise EmailListError(
            "Failed to count emails while paginating the mailbox listing."
        ) from exc

    return EmailPageOut(
        items=[row_to_email_metadata_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def count_unread_emails(
    mailbox_id: str,
    user_id: str,
    box: str = "ALL_MAIL",
) -> UnreadCountOut:
    """Count unread messages for a mailbox + box, with per-account breakdown.

    Local-only (no provider call). ``box`` is restricted at the router to
    ALL_MAIL | SPAM. Returns the mailbox-wide ``total`` plus one
    ``AccountUnreadDetail`` per account of the mailbox (0 included), so the
    frontend can feed the sidebar badge (total), the per-account tabs and
    the connected-accounts cards from a single response per (mailbox, box).
    Counts INDIVIDUAL messages (not threads) and reflects only the locally
    synced copy.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account listing error during unread count for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise UnreadCountError(
            "Failed to load mailbox accounts for unread count."
        ) from exc

    account_ids = [str(a["account_id"]) for a in accounts]
    if not account_ids:
        return UnreadCountOut(mailbox_id=mailbox_id, box=box, total=0, accounts=[])

    try:
        counts = email_metadata_store.count_unread_by_account(account_ids, box)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected unread count error for mailbox '%s' box '%s' (%s): %s",
            mailbox_id, box, type(exc).__name__, exc,
        )
        raise UnreadCountError(
            "Failed to count unread emails while building the mailbox unread badge."
        ) from exc

    # Fill 0 for accounts with no unread rows (GROUP BY omits them). Order
    # follows ``list_by_mailbox`` (the same stable order the listing uses).
    details = [
        AccountUnreadDetail(account_id=aid, unread=counts.get(aid, 0))
        for aid in account_ids
    ]
    total = sum(d.unread for d in details)

    return UnreadCountOut(
        mailbox_id=mailbox_id, box=box, total=total, accounts=details,
    )


# ---------------------------------------------------------------------------
# Favourites — Gmail STARRED label / Outlook flag.
# ---------------------------------------------------------------------------


def set_favorite(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    favorite: bool,
    user_id: str,
) -> FavoriteUpdateResponse:
    """Toggle the favourite flag at the provider and persist locally.

    Provider-First Rule: the provider call runs first; only on success
    is ``is_favorite`` updated on the local ``email_metadata`` row.
    If the local row does not exist, surface 404 ``email_not_found``
    BEFORE the provider call (saves the round trip).
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during favorite toggle (%s): %s",
            type(exc).__name__, exc,
        )
        raise FavoriteUpdateError(
            "Failed to look up account for favourite toggle."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during favourite toggle."
        )

    try:
        exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata existence check during favorite toggle (%s): %s",
            type(exc).__name__, exc,
        )
        raise FavoriteUpdateError(
            "Failed to verify email existence for favourite toggle."
        ) from exc
    if not exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            "during favourite toggle."
        )

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=FavoriteUpdateError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=FavoriteUpdateError)

        try:
            manager.set_favorite(account_label, provider_message_id, favorite)
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=FavoriteUpdateError,
                context={
                    "account_id": account_id,
                    "provider_message_id": provider_message_id,
                },
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider set_favorite (%s): %s",
                type(exc).__name__, exc,
            )
            raise FavoriteUpdateError(
                "Unexpected provider failure while toggling favourite at provider."
            ) from exc

        try:
            updated = email_metadata_store.update_favorite(
                account_id, provider_message_id, favorite,
            )
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Provider favorite toggle succeeded but DB persist failed (%s): %s",
                type(exc).__name__, exc,
            )
            raise FavoriteUpdateError(
                "Failed to persist favourite toggle in database."
            ) from exc
        if not updated:
            # Race: the row was deleted between the existence pre-check
            # and the update. Surface as 404 instead of silently
            # succeeding so callers can refresh.
            raise EmailNotFound(
                f"Email '{provider_message_id}' disappeared during favourite toggle "
                f"for account '{account_id}'."
            )

        return FavoriteUpdateResponse(
            provider_message_id=provider_message_id,
            account_id=account_id,
            is_favorite=favorite,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected favourite toggle error (%s): %s",
            type(exc).__name__, exc,
        )
        raise FavoriteUpdateError(
            "Failed to toggle email favourite flag."
        ) from exc


def sync_favorites(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
) -> FavoriteSyncResponse:
    """Reconcile ``is_favorite`` against the provider for one or every account.

    The sync ONLY updates rows that already exist locally (Option A in
    ``Ignore/Favoritos-Funcionalidad.md``): a favourite that exists at
    the provider but not yet in our local ``email_metadata`` is silently
    skipped — the general ``/emails/sync-metadata`` endpoint owns the
    job of importing brand-new rows.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during favorites sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise FavoriteSyncError(
                "Failed to look up account for favourites sync."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during favourites sync."
            )
        accounts = [account]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account listing error during favorites sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise FavoriteSyncError(
                "Failed to list accounts for favourites sync."
            ) from exc

    try:
        auth_payloads, label_lookup = _build_auth_context(accounts, mailbox_id)
        manager = build_manager_for_accounts(accounts)

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=FavoriteSyncError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=FavoriteSyncError)

        try:
            provider_results = manager.list_all_favorite_ids()
        except CoreError as exc:
            raise translate_core_error(exc, fallback=FavoriteSyncError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected list_all_favorite_ids error (%s): %s",
                type(exc).__name__, exc,
            )
            raise FavoriteSyncError(
                "Unexpected failure listing favourites from providers."
            ) from exc

        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=FavoriteSyncError)

        account_details: list[FavoriteSyncAccountDetail] = []
        total_synced = 0
        for label, favorite_ids in provider_results.items():
            ids = label_lookup.get(label)
            if not ids:
                continue
            _mailbox_id, aid, provider = ids
            try:
                affected = email_metadata_store.sync_favorites_for_account(
                    aid, favorite_ids,
                )
            except DatabaseError as exc:
                raise translate_database_error(exc) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected favourites sync DB error for account '%s' (%s): %s",
                    aid, type(exc).__name__, exc,
                )
                raise FavoriteSyncError(
                    "Failed to persist favourites sync result for an account."
                ) from exc

            total_synced += affected
            account_details.append(FavoriteSyncAccountDetail(
                account_id=aid,
                provider=provider,
                favorites_synced=len(favorite_ids),
            ))

        return FavoriteSyncResponse(
            total_synced=total_synced,
            accounts=account_details,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected favourites sync error (%s): %s",
            type(exc).__name__, exc,
        )
        raise FavoriteSyncError(
            "Failed to synchronise favourites."
        ) from exc


def _fetch_and_persist_email_content(
    manager: EmailManager,
    account_label: str,
    account_id: str,
    provider_message_id: str,
) -> EmailContentOut:
    """One provider read → sanitise + persist body (+TTL) + attachments → out.

    Shared by ``get_email_full_content`` (cache miss) AND the sync-time
    content prefetch. A SINGLE ``fetch_content_with_attachments`` read
    returns the body, the downloadable attachment list and the inline
    ``cid_map`` together (D4 — one provider round trip instead of two).

    The body and attachment persistence are best-effort (logged, never
    aborting): the provider read already succeeded, so a DB hiccup must
    not turn a readable email into a 502. The provider read itself is the
    one hard failure point — it raises ``EmailContentFetchError`` (502),
    which the cache-miss caller surfaces and the prefetch caller swallows.

    The prefetch MUST go through this full path (body AND attachments):
    persisting only the body would make the next open a cache HIT that
    never re-discovers attachments (discovery happens only here), hiding
    the clip and the attachments forever on pre-cached mail.
    """
    try:
        content, metadata_list, _cid_map = manager.fetch_content_with_attachments(
            account_label, provider_message_id,
        )
    except CoreError as exc:
        raise translate_core_error(exc, fallback=EmailContentFetchError) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected error during provider fetch_content_with_attachments (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Unexpected provider failure while fetching email content with attachments."
        ) from exc

    sanitized_html = sanitize_email_html(content.html_body) if content.html_body else None

    # The upsert stamps ``last_accessed_at = now()`` for the new row, so a
    # fresh cache entry starts its 30-day TTL on persist (no separate touch).
    try:
        persist_email_content(
            account_id, provider_message_id, sanitized_html, content.text_body,
            fallback=EmailContentFetchError,
        )
    except Exception as exc:
        logger.warning(
            "Content fetched but DB persist failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )

    _persist_attachment_metadata(account_id, provider_message_id, metadata_list)
    try:
        recompute_has_attachments(
            account_id, provider_message_id, fallback=EmailContentFetchError,
        )
    except Exception as exc:
        logger.warning(
            "has_attachments recompute failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )

    attachments_out = _load_email_attachments_out(account_id, provider_message_id)
    return EmailContentOut(
        html_body=sanitized_html,
        text_body=content.text_body,
        attachments=attachments_out,
    )


def get_email_full_content(
    mailbox_id: str,
    provider_message_id: str,
    account_id: str,
    user_id: str,
) -> EmailContentOut:
    """Return full email body, fetching from provider on cache miss."""
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during content fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Failed to look up account for email content fetch."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during email content fetch."
        )

    # Defensive metadata existence check. Required since email_content now
    # has a composite FK to email_metadata; a missing metadata row would
    # otherwise surface as a 500 from the FK violation at upsert time.
    try:
        metadata_exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata existence check error during content fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError(
            "Failed to verify email existence for content fetch."
        ) from exc
    if not metadata_exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            f"in mailbox '{mailbox_id}' during email content fetch."
        )

    row = get_email_content(account_id, provider_message_id, fallback=EmailContentFetchError)
    if row is not None:
        # Cache hit on the HTML body. Refresh the sliding TTL: an open
        # counts as an access, so frequently-read mail never expires
        # (best-effort — a touch failure must not break the read, and it
        # touches ONLY ``last_accessed_at``, not ``fetched_at``).
        touch_email_content_last_accessed(account_id, provider_message_id)
        # The email_attachments table is the source of truth for the
        # attachment list (D-13). Reading it always (not only on miss)
        # keeps the response consistent if a TTL purge later wiped the
        # blobs but kept the metadata rows.
        attachments_out = _load_email_attachments_out(account_id, provider_message_id)
        return EmailContentOut(
            html_body=row["html_body"],
            text_body=row["text_body"],
            attachments=attachments_out,
        )

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailContentFetchError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailContentFetchError)

        # Single provider read (D4): body + attachments + cid_map together.
        # The shared helper sanitises, persists the body (with a fresh TTL),
        # persists/recomputes attachments, and returns the out model.
        return _fetch_and_persist_email_content(
            manager, account_label, account_id, provider_message_id,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected email content fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailContentFetchError("Failed to fetch email content.") from exc


def get_reply_context(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    action: str,
    user_id: str,
) -> ReplyContextOut:
    """Build the data the composer needs to open Reply / Reply All / Forward.

    Single Provider-call read (no DB mutations). Follows the standard
    cascade: ``ensure_mailbox_access`` → account lookup → metadata
    existence pre-check → silent auth → ``manager.fetch_reply_context``
    → recipient / subject / quoted-body computation in pure helpers
    → coherence guard for Gmail-bound replies → assemble
    :py:class:`ReplyContextOut`.

    The local-existence pre-check via ``email_metadata_store.exists``
    matches the favourites toggle pattern: a missing row collapses
    to 404 without spending a provider round trip.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if action not in ("reply", "reply_all", "forward"):
        raise EmailReplyContextError(
            f"Invalid reply action '{action}' while preparing reply context "
            f"for message '{provider_message_id}'.",
            detail={"action": action},
        )

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during reply context fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to look up account while preparing reply context."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during reply context fetch."
        )

    try:
        metadata_exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata existence check error during reply context fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to verify email existence for reply context fetch."
        ) from exc
    if not metadata_exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            f"in mailbox '{mailbox_id}' during reply context fetch."
        )

    provider = str(account.get("provider") or "").lower()
    current_email = (account.get("email_address") or "").strip() or None

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=EmailReplyContextError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=EmailReplyContextError)

        try:
            reply_context = manager.fetch_reply_context(account_label, provider_message_id)
        except CoreError as exc:
            raise translate_core_error(exc, fallback=EmailReplyContextError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider fetch_reply_context (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailReplyContextError(
                "Unexpected provider failure while fetching reply context."
            ) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected reply context fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise EmailReplyContextError(
            "Failed to prepare reply context for the composer."
        ) from exc

    to_recipients, cc_recipients = compute_reply_recipients(
        original_from=reply_context.from_email,
        original_reply_to=reply_context.reply_to,
        original_to=reply_context.to_recipients,
        original_cc=reply_context.cc_recipients,
        current_account_email=current_email,
        action=action,
        original_box=reply_context.box,
    )

    new_subject = build_reply_subject(reply_context.subject, action)
    in_reply_to, references = build_in_reply_to_and_references(
        reply_context.message_id, reply_context.references,
    )
    quoted_body = build_quoted_body_html(
        reply_context.body_html,
        reply_context.body_text,
        from_name=reply_context.from_name,
        from_email=reply_context.from_email,
        received_at=reply_context.received_at,
        action=action,
        to_recipients=reply_context.to_recipients,
        cc_recipients=reply_context.cc_recipients,
        subject=reply_context.subject,
    )

    # Gmail-bound replies must satisfy the triple-requirement guard
    # (threadId + In-Reply-To/References + matching Subject). For
    # Forward we skip the subject check because the prefix changes
    # ("Fwd:" vs original) and Gmail does not require subject match
    # for forwards (the user reaches new recipients with a new id).
    if provider == "gmail" and action in ("reply", "reply_all") and reply_context.thread_id:
        try:
            validate_reply_threading_coherence(
                thread_id=reply_context.thread_id,
                in_reply_to=in_reply_to,
                references=references,
                original_message_id=reply_context.message_id,
                original_thread_id=reply_context.thread_id,
                original_subject=reply_context.subject,
                new_subject=new_subject,
            )
        except CoreError as exc:
            raise translate_core_error(exc, fallback=EmailReplyContextError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during reply threading coherence check (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailReplyContextError(
                "Unexpected failure during reply threading coherence check."
            ) from exc

    return ReplyContextOut(
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        bcc_recipients=[],
        subject=new_subject,
        body=quoted_body,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=reply_context.thread_id,
        reply_to_message_id=reply_context.provider_message_id,
        reply_kind=action,  # type: ignore[arg-type]
        original_from_email=reply_context.from_email,
    )


def _conversation_message_to_metadata(
    message: ConversationMessage, account_id: str,
) -> EmailMetadata:
    """Convert a provider ``ConversationMessage`` into a syncable
    ``EmailMetadata`` (drops ``is_favorite`` — applied separately via
    ``update_favorite`` — and stamps ``account_id`` like the sync path)."""
    return EmailMetadata(
        provider_message_id=message.provider_message_id,
        thread_id=message.thread_id,
        from_email=message.from_email,
        from_name=message.from_name,
        subject=message.subject,
        received_at=message.received_at,
        is_read=message.is_read,
        box=message.box,
        to_email=message.to_email,
        to_name=message.to_name,
        account_id=account_id,
    )


def _conversation_message_to_out(
    message: ConversationMessage, account_id: str, mailbox_id: str,
) -> EmailMetadataOut:
    """Map a provider ``ConversationMessage`` into the viewer response model.

    Uses the provider's fresh per-message state (box / is_read / favourite),
    the account's ``account_id`` / ``mailbox_id`` (a thread never crosses
    accounts), and ``has_attachments=False`` (B.lazy — the per-message clip
    appears once the body is opened and attachments are discovered).
    """
    return EmailMetadataOut(
        provider_message_id=message.provider_message_id,
        account_id=account_id,
        mailbox_id=mailbox_id,
        thread_id=message.thread_id,
        from_email=message.from_email,
        from_name=message.from_name or None,
        to_email=message.to_email or None,
        to_name=message.to_name or None,
        subject=message.subject,
        received_at=message.received_at,
        is_read=message.is_read,
        box=message.box,
        has_attachments=False,
        is_favorite=message.is_favorite,
    )


def get_conversation(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    user_id: str,
) -> ConversationOut:
    """Return the full message chain of a conversation (conversation viewer).

    Read + lazy sync (not Provider-First — it only reads from the provider
    and completes the local copy, like ``get_email_full_content`` filling
    ``email_content``). Cascade:

    1. ``ensure_mailbox_access`` (ownership).
    2. Account lookup → 404 ``account_not_found`` when missing.
    3. Read the base message row (incl. ``thread_id``) via ``get_metadata``
       → 404 ``email_not_found`` when missing (it must be synced: the user
       clicked a listed row).
    4. ``thread_id`` empty → single-message conversation, mapped from the
       row already read, NO provider call.
    5. Otherwise: silent auth → ``manager.fetch_conversation`` → best-effort
       lazy sync (persist the thread's messages, applying favourites) →
       build the response from the provider's fresh state ordered oldest
       first.
    """
    # Re-canonicalise mailbox_id from the authoritative DB record instead of
    # trusting the path param verbatim. ensure_mailbox_access already proved the
    # param resolves to this owned row, so this only normalises its exact form
    # (no behavioural change) before it is threaded into every downstream call.
    mailbox_record = ensure_mailbox_access(mailbox_id, user_id)
    mailbox_id = str(mailbox_record.get("mailbox_id") or mailbox_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during conversation fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise ConversationFetchError(
            "Failed to look up account for conversation fetch."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during conversation fetch."
        )

    try:
        base_row = email_metadata_store.get_metadata(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected metadata lookup error during conversation fetch (%s): %s",
            type(exc).__name__, exc,
        )
        raise ConversationFetchError(
            "Failed to load base message metadata for conversation fetch."
        ) from exc
    if base_row is None:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' "
            f"in mailbox '{mailbox_id}' during conversation fetch."
        )

    thread_id = str(base_row.get("thread_id") or "")
    if not thread_id:
        # Single-message conversation: no provider thread to fetch. Map the
        # base message directly from the row already read (no second read).
        return ConversationOut(
            thread_id="",
            messages=[row_to_email_metadata_out(base_row)],
        )

    try:
        auth_payloads, label_lookup = _build_auth_context([account], mailbox_id)
        manager = build_manager_for_accounts([account])
        account_label = f"{mailbox_id}__{account_id}"

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=ConversationFetchError)
        raise_on_silent_auth_errors(manager.get_last_errors(), fallback=ConversationFetchError)

        try:
            members = manager.fetch_conversation(account_label, thread_id)
        except CoreError as exc:
            raise translate_core_error(exc, fallback=ConversationFetchError) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider fetch_conversation (%s): %s",
                type(exc).__name__, exc,
            )
            raise ConversationFetchError(
                "Unexpected provider failure while fetching the conversation thread."
            ) from exc

        # Lazy sync ("complete the mailbox"): persist every thread message so
        # reopening serves from DB and the bodies pre-check passes. Best-effort
        # — the viewer must open even if the cache-fill fails.
        _lazy_sync_conversation(account_id, members)

        # Map the response from the provider's fresh state (NOT a DB re-read):
        # a message that moved box / was read out-of-band is reflected even if
        # the best-effort upsert above failed. Oldest first.
        ordered = sorted(
            members, key=lambda m: (m.received_at, m.provider_message_id),
        )
        messages = [
            _conversation_message_to_out(m, account_id, mailbox_id) for m in ordered
        ]
        return ConversationOut(thread_id=thread_id, messages=messages)
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected conversation fetch error (%s): %s",
            type(exc).__name__, exc,
        )
        raise ConversationFetchError("Failed to fetch conversation.") from exc


def _lazy_sync_conversation(
    account_id: str, members: list[ConversationMessage],
) -> None:
    """Best-effort persistence of a fetched conversation's messages.

    Upserts every message into ``email_metadata`` (inserting the ones never
    synced, refreshing ``is_read`` / ``box`` / ``to_*`` of existing ones —
    the shared upsert never touches ``thread_id`` / ``is_favorite`` /
    ``has_attachments``) and re-applies the provider's favourite flag in a
    single batch UPDATE over the thread's favourite members (one-directional
    — never clears FALSE). Every step is swallowed on failure (logged) so a
    cache-fill hiccup never aborts the viewer.

    NOTE: this only affects the LISTING's thread row on the next list; it
    does NOT change the ``ConversationOut`` of this call (the viewer reads
    per-message state from the provider members, not from the DB).
    """
    if not members:
        return
    metadata_list = [
        _conversation_message_to_metadata(m, account_id) for m in members
    ]
    try:
        persist_email_metadata_batch(
            account_id, metadata_list, fallback=ConversationFetchError,
        )
    except Exception as exc:
        logger.warning(
            "Conversation lazy-sync metadata persist failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )
        return

    # The shared upsert does not touch ``is_favorite``; re-apply the
    # provider's favourite state so the listing's thread-level favourite
    # aggregate is correct on the next list. A SINGLE batch statement marks
    # every favourite thread member TRUE (avoids an UPDATE per message — the
    # old N+1). One-directional by design: un-starring is reconciled by the
    # favourites toggle / sync, never by opening a conversation. Soft-fail.
    favourite_ids = [m.provider_message_id for m in members if m.is_favorite]
    if not favourite_ids:
        return
    try:
        email_metadata_store.set_favorites_true_batch(account_id, favourite_ids)
    except Exception as exc:
        logger.warning(
            "Conversation lazy-sync favourite batch apply failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
        )


def _persist_attachment_metadata(
    account_id: str,
    provider_message_id: str,
    metadata_list: list[Any],
) -> None:
    """Upsert the discovered attachment list into ``email_attachments`` (D-09).

    Soft-fail on persistence: a transient DB error here would block the
    user from reading the body just because the list could not be
    cached. The next ``get_email_content`` call (cache miss again on
    the body side) will retry.
    """
    if not metadata_list:
        return
    # B-SANITIZE: received attachments must go through the same filename
    # sanitisation as drafts (D-20) — neutralise path traversal / reserved
    # characters / reserved Windows names and resolve duplicates within the
    # same message with `` (1)``, `` (2)``. ``mime_type`` is already resolved
    # in the provider client (B-MIME / B-OUTLOOK-LOWER), so it is persisted
    # as-is.
    seen_names: list[str] = []
    rows = []
    for meta in metadata_list:
        safe_name = sanitize_filename(meta.filename, existing=seen_names)
        seen_names.append(safe_name)
        rows.append(
            {
                "attachment_id": str(uuid.uuid4()),
                "account_id": account_id,
                "provider_message_id": provider_message_id,
                "part_id": meta.part_id,
                "provider_attachment_id": meta.provider_attachment_id,
                "filename": safe_name,
                "mime_type": meta.mime_type,
                "size": meta.size,
                "content_id": meta.content_id,
                "is_inline": meta.is_inline,
                "position": meta.position,
            }
        )
    try:
        email_attachment_store.upsert_batch(rows)
    except DatabaseError as exc:
        logger.warning(
            "Attachment metadata upsert failed (%s): %s",
            type(exc).__name__, exc,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected attachment metadata upsert error (%s): %s",
            type(exc).__name__, exc,
        )


def _load_email_attachments_out(
    account_id: str, provider_message_id: str,
) -> list[AttachmentMetadataOut]:
    """List ``email_attachments`` rows mapped to the API output schema.

    Used both at cache hit and after a fresh upsert so the returned
    list always carries the derived ``is_downloaded`` flag (true iff a
    blob row exists). Soft-fails to an empty list — the user still
    sees the email body even if the metadata read hiccups.
    """
    try:
        rows = email_attachment_store.list_by_message(account_id, provider_message_id)
    except DatabaseError as exc:
        logger.warning(
            "Failed to list email attachments for content view (%s): %s",
            type(exc).__name__, exc,
        )
        return []
    except Exception as exc:
        logger.warning(
            "Unexpected error listing email attachments for content view (%s): %s",
            type(exc).__name__, exc,
        )
        return []
    return [
        AttachmentMetadataOut(
            attachment_id=str(row["attachment_id"]),
            filename=str(row.get("filename") or "attachment"),
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            size=int(row.get("size") or 0),
            is_downloaded=bool(row.get("is_downloaded", False)),
            is_unavailable=row.get("unavailable_at") is not None,
            position=int(row.get("position") or 0),
        )
        for row in rows
    ]
