"""
Service layer for user folders (carpetas-y-reglas).

Owns the folder CRUD, the per-email assign / unassign (Provider-First), and the
rename / delete reflection to the provider. A folder is user-level (aggregates
every account of the user); it materialises lazily per account as a Gmail
user-label / Outlook category (see ``services_helpers.carpetas``).

Ownership model: a folder belongs to a user, not a mailbox — a foreign folder
collapses to 404 ``folder_not_found`` (D-22). Per-email operations additionally
run ``ensure_mailbox_access`` and resolve the message's own account.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    EmailNotFound,
    FolderNameConflict,
    FolderNotFound,
    FolderOperationError,
)
from api.schemas.email import EmailPageOut
from api.schemas.folder import (
    EmailFoldersOut,
    FolderCreate,
    FolderOut,
    FolderUpdate,
)
from api.services.services_helpers import (
    assign_folder_provider_first,
    authenticate_single_account,
    enrich_items_with_folders,
    ensure_mailbox_access,
    fetch_folder_chips,
    parse_search_query,
    row_to_email_metadata_out,
    translate_database_error,
    unassign_folder_provider_first,
)
from database import (
    account_store,
    DatabaseError,
    email_metadata_store,
    folder_store,
)


# ---------------------------------------------------------------------------
# Ownership + name-conflict helpers
# ---------------------------------------------------------------------------


def _load_owned_folder(folder_id: str, user_id: str) -> dict[str, Any]:
    try:
        record = folder_store.get(folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder lookup error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to look up folder.") from exc
    if record is None or str(record.get("owner_user_id")) != str(user_id):
        raise FolderNotFound(f"Folder '{folder_id}' not found.")
    return record


def _list_owned_folders(user_id: str) -> list[dict[str, Any]]:
    try:
        return folder_store.list_by_owner(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder listing error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to list folders.") from exc


def _reject_name_conflict(
    user_id: str, name: str, *, exclude_folder_id: str | None = None,
) -> None:
    """Pre-check the case-insensitive name uniqueness for a clean 409 surface.

    The unique index ``(owner_user_id, lower(name))`` is the hard defence; this
    pre-check is the friendly 409 for the common case. A rare create/create race
    that slips past this still hits the index (surfaced as a 503 QueryError).
    """
    target = name.strip().lower()
    for folder in _list_owned_folders(user_id):
        if str(folder.get("folder_id")) == str(exclude_folder_id):
            continue
        if str(folder.get("name", "")).strip().lower() == target:
            raise FolderNameConflict(
                f"A folder named '{name}' already exists.",
                {"name": name},
            )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_folder(user_id: str, payload: FolderCreate) -> FolderOut:
    """Create a folder row. Does NOT materialise at the provider — that happens
    lazily on first assignment (decision 3/12)."""
    _reject_name_conflict(user_id, payload.name)
    row_input = {
        "folder_id": str(uuid.uuid4()),
        "owner_user_id": user_id,
        "name": payload.name,
        "color": payload.color,
    }
    try:
        record = folder_store.create(row_input)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder create error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to create folder.") from exc
    return FolderOut(**record)


def list_folders(user_id: str) -> list[FolderOut]:
    return [FolderOut(**row) for row in _list_owned_folders(user_id)]


def get_folder(folder_id: str, user_id: str) -> FolderOut:
    return FolderOut(**_load_owned_folder(folder_id, user_id))


def update_folder(folder_id: str, user_id: str, payload: FolderUpdate) -> FolderOut:
    """Rename and/or recolour. A rename reflects to the provider (Gmail patches
    the label; Outlook re-tags the member messages); a recolour is local only."""
    current = _load_owned_folder(folder_id, user_id)
    old_name = str(current.get("name") or "")
    new_name = payload.name if payload.name is not None else old_name
    new_color = payload.color if payload.color is not None else current.get("color")
    name_changed = payload.name is not None and payload.name != old_name

    if name_changed:
        _reject_name_conflict(user_id, new_name, exclude_folder_id=folder_id)
        # Provider-First (best-effort per account): reflect the rename BEFORE the
        # local write so future assigns use the new ref (Outlook link ref).
        _reflect_folder_rename(folder_id, user_id, old_name, new_name)

    try:
        record = folder_store.update({
            "folder_id": folder_id,
            "name": new_name,
            "color": new_color,
        })
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder update error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to update folder.") from exc
    if record is None:
        raise FolderNotFound(f"Folder '{folder_id}' disappeared during update.")
    return FolderOut(**record)


def delete_folder(folder_id: str, user_id: str) -> None:
    """Delete a folder. Reflects to the provider best-effort (Gmail deletes the
    label; Outlook removes the category from each member), then cascades the
    local rows (folder_account_links, email_folder_members, rules,
    rule_apply_jobs — all via ON DELETE CASCADE). Deleting a folder never
    deletes the emails themselves."""
    _load_owned_folder(folder_id, user_id)
    _reflect_folder_delete(folder_id, user_id)
    try:
        deleted = folder_store.delete(folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder delete error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to delete folder.") from exc
    if not deleted:
        raise FolderNotFound(f"Folder '{folder_id}' disappeared before delete.")


# ---------------------------------------------------------------------------
# Provider reflection for rename / delete (best-effort, per materialised account)
# ---------------------------------------------------------------------------


def _folder_links(folder_id: str) -> list[dict[str, Any]]:
    try:
        return folder_store.list_links_by_folder(folder_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder links list error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to list folder materialisations.") from exc


def _reflect_folder_rename(
    folder_id: str, user_id: str, old_name: str, new_name: str,
) -> None:
    """Reflect a rename at every materialised account. Best-effort per account —
    a dead token or provider error is logged and skipped so the local rename
    still succeeds. Outlook re-tags each member (its category name is immutable)
    and repoints the stored link ref; Gmail patches the label id in place."""
    for link in _folder_links(folder_id):
        account_id = str(link["account_id"])
        provider_ref = str(link["provider_ref"])
        try:
            account = account_store.get_by_id_for_user(account_id, user_id)
            if account is None:
                continue
            mailbox_id = str(account["mailbox_id"])
            provider = str(account.get("provider") or "").lower()
            manager, account_label = authenticate_single_account(
                account, mailbox_id, fallback=FolderOperationError,
            )
            if provider == "gmail":
                manager.rename_folder_label(account_label, provider_ref, new_name)
            else:
                for message_id in folder_store.list_member_message_ids(folder_id, account_id):
                    manager.unassign_folder_from_message(
                        account_label, message_id, provider_ref, old_name,
                    )
                    manager.assign_folder_to_message(
                        account_label, message_id, new_name, new_name,
                    )
                folder_store.update_link_ref(folder_id, account_id, new_name)
        except Exception as exc:
            logger.warning(
                "Folder rename reflection failed for account %s (%s): %s",
                account_id, type(exc).__name__, exc, exc_info=exc,
            )


def _reflect_folder_delete(folder_id: str, user_id: str) -> None:
    """Reflect a delete at every materialised account. Best-effort per account."""
    for link in _folder_links(folder_id):
        account_id = str(link["account_id"])
        provider_ref = str(link["provider_ref"])
        try:
            account = account_store.get_by_id_for_user(account_id, user_id)
            if account is None:
                continue
            mailbox_id = str(account["mailbox_id"])
            provider = str(account.get("provider") or "").lower()
            manager, account_label = authenticate_single_account(
                account, mailbox_id, fallback=FolderOperationError,
            )
            if provider == "gmail":
                manager.delete_folder_label(account_label, provider_ref)
            else:
                for message_id in folder_store.list_member_message_ids(folder_id, account_id):
                    manager.unassign_folder_from_message(
                        account_label, message_id, provider_ref, provider_ref,
                    )
        except Exception as exc:
            logger.warning(
                "Folder delete reflection failed for account %s (%s): %s",
                account_id, type(exc).__name__, exc, exc_info=exc,
            )


# ---------------------------------------------------------------------------
# Per-email assign / unassign (Provider-First)
# ---------------------------------------------------------------------------


def _resolve_email_account(
    mailbox_id: str, account_id: str, provider_message_id: str, user_id: str,
) -> dict[str, Any]:
    """Ownership chain for a per-email folder op: mailbox access → account in
    mailbox → the message row exists. A foreign account / missing message both
    collapse to 404 (D-22 / the set_favorite pre-check)."""
    ensure_mailbox_access(mailbox_id, user_id)
    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected account lookup error during folder op (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to look up account for folder assignment.") from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' during folder assignment."
        )
    try:
        exists = email_metadata_store.exists(account_id, provider_message_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected email existence check error during folder op (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to verify email existence for folder assignment.") from exc
    if not exists:
        raise EmailNotFound(
            f"Email '{provider_message_id}' not found for account '{account_id}' during folder assignment."
        )
    return account


def _email_folders_out(provider_message_id: str, account_id: str) -> EmailFoldersOut:
    chips = fetch_folder_chips([(provider_message_id, account_id)])
    return EmailFoldersOut(folders=chips.get((provider_message_id, account_id), []))


def assign_folder_to_email(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    folder_id: str,
    user_id: str,
) -> EmailFoldersOut:
    """Assign a folder to a single email (Provider-First)."""
    account = _resolve_email_account(mailbox_id, account_id, provider_message_id, user_id)
    folder = _load_owned_folder(folder_id, user_id)
    manager, account_label = authenticate_single_account(
        account, mailbox_id, fallback=FolderOperationError,
    )
    assign_folder_provider_first(
        manager, account_label, account_id, provider_message_id,
        folder_id, str(folder["name"]), fallback=FolderOperationError,
    )
    return _email_folders_out(provider_message_id, account_id)


def unassign_folder_from_email(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    folder_id: str,
    user_id: str,
) -> EmailFoldersOut:
    """Remove a folder from a single email (Provider-First)."""
    account = _resolve_email_account(mailbox_id, account_id, provider_message_id, user_id)
    folder = _load_owned_folder(folder_id, user_id)
    manager, account_label = authenticate_single_account(
        account, mailbox_id, fallback=FolderOperationError,
    )
    unassign_folder_provider_first(
        manager, account_label, account_id, provider_message_id,
        folder_id, str(folder["name"]), fallback=FolderOperationError,
    )
    return _email_folders_out(provider_message_id, account_id)


# ---------------------------------------------------------------------------
# Folder email listing (unified across the user's accounts)
# ---------------------------------------------------------------------------


def list_folder_emails(
    folder_id: str,
    user_id: str,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> EmailPageOut:
    """Return a page of the emails that belong to a folder, across all of the
    user's accounts. Reuses the SAME listing machinery as the virtual mailbox
    (dedup + group-by-thread) via ``list_filtered(folder_id=...)``. Shows the
    folder's members in every real box except the internal ``DELETED`` sentinel
    (a positive ``in:`` box override narrows it further)."""
    _load_owned_folder(folder_id, user_id)

    try:
        account_ids = account_store.list_account_ids_by_user(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected owned-accounts listing error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to list owned accounts for folder listing.") from exc
    if not account_ids:
        return EmailPageOut(items=[], total=0, limit=limit, offset=offset)

    parsed = parse_search_query(q)
    tokens = parsed.tokens
    operator_clauses = parsed.operator_clauses

    # Membership is orthogonal to box: show the folder's members in every real
    # box, only ever excluding the internal DELETED hard-delete sentinel (never
    # shown anywhere). An explicit ``in:`` narrows to that single box instead.
    box: str | None = None
    box_not_in: list[str] | None = ["DELETED"]
    if parsed.box_override is not None:
        box = parsed.box_override
        box_not_in = None

    try:
        rows = email_metadata_store.list_filtered(
            account_ids, box, tokens, limit, offset,
            box_not_in=box_not_in,
            operator_clauses=operator_clauses or None,
            distinct_provider_message_id=True,
            group_by_thread=True,
            folder_id=folder_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder email listing error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to list emails for folder.") from exc

    try:
        total = email_metadata_store.count_filtered(
            account_ids, box, tokens,
            box_not_in=box_not_in,
            operator_clauses=operator_clauses or None,
            distinct_provider_message_id=True,
            group_by_thread=True,
            folder_id=folder_id,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning("Unexpected folder email count error (%s): %s", type(exc).__name__, exc)
        raise FolderOperationError("Failed to count emails while paginating the folder listing.") from exc

    items = [row_to_email_metadata_out(row) for row in rows]
    enrich_items_with_folders(items)
    return EmailPageOut(items=items, total=total, limit=limit, offset=offset)
