"""Favoritos (Gmail STARRED / Outlook flag): toggle Provider-First y sincronizacion con el proveedor."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailNotFound,
    FavoriteSyncError,
    FavoriteUpdateError,
)
from core.email import CoreError
from api.schemas.email import (
    FavoriteSyncAccountDetail,
    FavoriteSyncResponse,
    FavoriteUpdateResponse,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    email_metadata_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


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
