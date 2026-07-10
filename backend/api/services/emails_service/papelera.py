"""Papelera: borrado permanente / restauracion (manage_trash) y envio a papelera (move_to_trash)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailNotInTrash,
    MoveToTrashError,
    TrashOperationError,
)
from core.email import CoreError
from api.schemas.email import (
    MoveToTrashRequest,
    MoveToTrashResult,
    TrashActionRequest,
    TrashActionResult,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    get_trash_emails_by_ids,
    mark_as_deleted_batch,
    move_to_trash_batch,
    raise_on_silent_auth_errors,
    restore_from_trash_batch,
    restore_from_trash_discovered_batch,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


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
