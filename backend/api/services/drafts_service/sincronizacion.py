"""Sincronizacion de borradores desde el proveedor a la tabla local."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    DraftSyncError,
)
from api.schemas.draft import (
    DraftsAccountSyncDetail,
    DraftsSyncResultOut,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
)
from core.email import CoreError
from database import (
    account_store,
    draft_store,
    DatabaseError,
)

from ._comunes import _build_draft_auth_context, _persist_refreshed_tokens


def sync_drafts(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
) -> DraftsSyncResultOut:
    """
    Load drafts from the provider(s) into the local drafts table.

    If account_id is None, syncs every account in the mailbox; otherwise
    only that specific account. Ownership enforced via ensure_mailbox_access.
    Per account, the full provider draft list replaces the local rows
    atomically (upsert + delete-missing). Both providers cap the fetch
    at _DRAFTS_MAX_TOTAL = 100 drafts per account.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # Step 1: load accounts (single or full mailbox)
    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during draft sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftSyncError(
                "Failed to look up account for draft sync."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during draft sync."
            )
        accounts = [account]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account list error during draft sync (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftSyncError(
                "Failed to list accounts for draft sync."
            ) from exc

    if not accounts:
        return DraftsSyncResultOut(total_synced=0, accounts=[])

    # Step 2: build manager + silent auth
    try:
        auth_payloads, label_lookup = _build_draft_auth_context(accounts, mailbox_id)
        manager = build_manager_for_accounts(accounts)
        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=DraftSyncError)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftSyncError,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected error during draft sync setup (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError(
            "Failed to prepare draft sync context."
        ) from exc

    # Step 3: fetch drafts from all clients
    try:
        fetch_results = manager.fetch_all_drafts()
    except CoreError as exc:
        raise translate_core_error(
            exc, fallback=DraftSyncError,
        ) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected fetch_all_drafts error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError(
            "Failed to fetch drafts from providers."
        ) from exc

    try:
        # Step 4: surface per-account errors captured by the manager
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftSyncError,
        )

        # Step 5: persist per account (atomic replace)
        account_details: list[DraftsAccountSyncDetail] = []
        total_synced = 0
        for label, drafts in fetch_results.items():
            ids = label_lookup.get(label)
            if not ids:
                continue
            _, account_id_inner, provider = ids
            rows = [
                {
                    "provider_draft_id": d.provider_draft_id,
                    "to_recipients": list(d.to_recipients),
                    "cc_recipients": list(d.cc_recipients),
                    "bcc_recipients": list(d.bcc_recipients),
                    "subject": d.subject,
                    "body": d.body,
                    "created_at": d.created_at,
                    "updated_at": d.updated_at,
                }
                for d in drafts
            ]
            try:
                synced_count = draft_store.replace_all_for_account(account_id_inner, rows)
            except DatabaseError as exc:
                raise translate_database_error(exc) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected persist error during draft sync for account '%s' (%s): %s",
                    account_id_inner, type(exc).__name__, exc,
                )
                raise DraftSyncError(
                    "Failed to persist drafts during sync."
                ) from exc

            total_synced += synced_count
            account_details.append(DraftsAccountSyncDetail(
                account_id=account_id_inner,
                provider=provider,
                drafts_synced=synced_count,
            ))

        return DraftsSyncResultOut(
            total_synced=total_synced,
            accounts=account_details,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft sync error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftSyncError("Unexpected failure during draft sync.") from exc
