"""Movimientos de buzon (Provider-First): spam / archivo y su motor compartido _execute_box_move_operation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    ArchiveMoveError,
    ArchiveRestoreError,
    SpamMoveError,
    SpamRestoreError,
)
from core.email import CoreError
from api.schemas.email import (
    AccountArchiveDetail,
    AccountSpamDetail,
    ArchiveRequest,
    ArchiveResponse,
    SpamItem,
    SpamRequest,
    SpamResponse,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    raise_on_silent_auth_errors,
    translate_core_error,
    translate_database_error,
    update_email_spam_status_batch,
)
from database import (
    account_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


def move_to_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str,
) -> SpamResponse:
    """Move emails to spam across accounts in a mailbox."""
    moved_count, details = _execute_box_move_operation(
        mailbox_id, payload.items, user_id,
        manager_method="move_to_spam",
        target_box="SPAM",
        fallback_error=SpamMoveError,
        operation_label="spam move",
    )
    return SpamResponse(
        moved_count=moved_count,
        accounts=[AccountSpamDetail(account_id=a, moved=m) for a, m in details],
    )


def restore_from_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str,
) -> SpamResponse:
    """Restore emails from spam across accounts in a mailbox."""
    moved_count, details = _execute_box_move_operation(
        mailbox_id, payload.items, user_id,
        manager_method="restore_from_spam",
        target_box="ALL_MAIL",
        fallback_error=SpamRestoreError,
        operation_label="spam restore",
    )
    return SpamResponse(
        moved_count=moved_count,
        accounts=[AccountSpamDetail(account_id=a, moved=m) for a, m in details],
    )


def move_to_archive(
    mailbox_id: str,
    payload: ArchiveRequest,
    user_id: str,
) -> ArchiveResponse:
    """Archive emails across accounts in a mailbox (Provider-First)."""
    moved_count, details = _execute_box_move_operation(
        mailbox_id, payload.items, user_id,
        manager_method="move_to_archive",
        target_box="ARCHIVE",
        fallback_error=ArchiveMoveError,
        operation_label="archive move",
    )
    return ArchiveResponse(
        moved_count=moved_count,
        accounts=[AccountArchiveDetail(account_id=a, moved=m) for a, m in details],
    )


def restore_from_archive(
    mailbox_id: str,
    payload: ArchiveRequest,
    user_id: str,
) -> ArchiveResponse:
    """Unarchive emails across accounts in a mailbox (back to the inbox)."""
    moved_count, details = _execute_box_move_operation(
        mailbox_id, payload.items, user_id,
        manager_method="restore_from_archive",
        target_box="ALL_MAIL",
        fallback_error=ArchiveRestoreError,
        operation_label="archive restore",
    )
    return ArchiveResponse(
        moved_count=moved_count,
        accounts=[AccountArchiveDetail(account_id=a, moved=m) for a, m in details],
    )


def _execute_box_move_operation(
    mailbox_id: str,
    items: list[SpamItem],
    user_id: str,
    *,
    manager_method: str,
    target_box: str,
    fallback_error: type[ApiError],
    operation_label: str,
) -> tuple[int, list[tuple[str, int]]]:
    """Shared engine for provider box-move operations (spam / archive).

    Groups ``items`` by account, validates ``account ∈ mailbox``, runs the
    silent-auth sequence, calls ``manager.<manager_method>`` per account,
    translates errors and persists the box move (old→new id rewrite +
    ``box = target_box``) via ``update_email_spam_status_batch``. Returns a
    schema-neutral ``(moved_count, [(account_id, moved), ...])`` so each
    public wrapper can build its own response schema. ``items`` is typed as
    ``list[SpamItem]`` because ``SpamItem`` / ``ArchiveItem`` are structurally
    identical (``account_id`` / ``provider_message_id``).
    """
    ensure_mailbox_access(mailbox_id, user_id)

    items_by_account: dict[str, list[str]] = {}
    for item in items:
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

        account_details: list[tuple[str, int]] = []
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

            account_details.append((aid, len(results)))
            total_moved += len(results)

        return total_moved, account_details
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected %s error (%s): %s",
            operation_label, type(exc).__name__, exc,
        )
        raise fallback_error(f"Failed to execute {operation_label}.") from exc
