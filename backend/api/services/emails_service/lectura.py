"""Marcado de correos como leidos / no leidos en las cuentas de un buzon."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    ReadStatusUpdateError,
)
from core.email import CoreError
from api.schemas.email import (
    AccountReadStatusDetail,
    ReadStatusRequest,
    ReadStatusResponse,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
    update_email_read_status_batch,
    update_email_read_status_by_thread,
)
from database import (
    account_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


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
                # Conversation viewer marks the whole thread (propagate_thread)
                # so Outlook's duplicate rows for one message all flip; the
                # per-message surfaces mark only the ids they sent.
                if payload.propagate_thread:
                    update_email_read_status_by_thread(
                        aid, updated_ids, payload.is_read, fallback=ReadStatusUpdateError,
                    )
                else:
                    update_email_read_status_batch(
                        aid, updated_ids, payload.is_read, fallback=ReadStatusUpdateError,
                    )

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
