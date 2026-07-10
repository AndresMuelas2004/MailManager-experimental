"""Gestion de borradores: crear, actualizar, borrar y listar (Provider-First)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    DraftCreationError,
    DraftDeleteError,
    DraftListError,
    DraftNotFound,
    DraftUpdateError,
)
from api.schemas.draft import (
    DraftCreate,
    DraftOut,
    DraftUpdate,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    load_wrapped_account_tokens,
    load_wrapped_app_credentials,
    raise_on_silent_auth_errors,
    sanitize_outbound_html,
    translate_core_error,
    translate_database_error,
)
from core.email import CoreError
from database import (
    account_store,
    draft_attachment_store,
    draft_store,
    DatabaseError,
)

from ._comunes import _draft_out_from_row, _persist_refreshed_tokens


def create_draft(
    mailbox_id: str,
    account_id: str,
    payload: DraftCreate,
    user_id: str,
) -> DraftOut:
    """
    Create a draft at the provider and persist it locally.
    Provider-First: only persist if the provider call succeeds.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # Sanitise the rich-text HTML body ONCE at entry (trust boundary) and
    # reuse the cleaned value for BOTH the provider call and the persisted
    # row — the two must never diverge. Fail-soft (never raises).
    sanitized_body = sanitize_outbound_html(payload.body)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft creation (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftCreationError(
            "Failed to look up account while creating draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft creation."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftCreationError,
        )

        try:
            draft_metadata = manager.create_draft(
                account_label,
                payload.to_recipients,
                payload.cc_recipients,
                payload.bcc_recipients,
                payload.subject,
                sanitized_body,
                thread_id=payload.thread_id,
                in_reply_to=payload.in_reply_to,
                references=payload.references_header,
                reply_to_message_id=payload.reply_to_message_id,
                reply_kind=payload.reply_kind,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftCreationError,
                context={"account_id": account_id, "account_label": account_label},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft creation (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftCreationError(
                "Unexpected failure while creating draft at provider."
            ) from exc

        row = {
            "provider_draft_id": draft_metadata.provider_draft_id,
            "account_id": account_id,
            "to_recipients": list(payload.to_recipients),
            "cc_recipients": list(payload.cc_recipients),
            "bcc_recipients": list(payload.bcc_recipients),
            "subject": payload.subject,
            "body": sanitized_body,
            "reply_kind": payload.reply_kind,
            "reply_to_message_id": payload.reply_to_message_id,
            "reply_to_account_id": (
                str(payload.reply_to_account_id) if payload.reply_to_account_id else None
            ),
            "thread_id": payload.thread_id,
            "in_reply_to": payload.in_reply_to,
            "references_header": payload.references_header,
        }
        try:
            persisted = draft_store.create(row)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB persist error (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftCreationError(
                "Failed to persist draft to database after provider creation."
            ) from exc

        # Outlook + Forward: ``createForward`` copies the original
        # message's attachments server-side. Discover them now and
        # persist the metadata rows in ``draft_attachments`` (no blob —
        # the bytes live in the provider draft until send) so the
        # composer renders the chips from first render. Gmail has no
        # such inheritance; the frontend will call
        # ``copy_attachments_from_email`` explicitly for Gmail.
        if provider == "outlook" and payload.reply_kind == "forward":
            _persist_outlook_forward_inherited_attachments(
                account_id=account_id,
                provider_draft_id=draft_metadata.provider_draft_id,
                manager=manager,
                account_label=account_label,
            )

        # Reload to pick up the just-inserted ``draft_attachments`` rows
        # — only matters for the Outlook Forward path; otherwise the
        # query returns an empty attachments list which matches the
        # behaviour before the inheritance step.
        return _draft_out_from_row(persisted)
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft creation error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftCreationError("Failed to create draft.") from exc


def _persist_outlook_forward_inherited_attachments(
    *,
    account_id: str,
    provider_draft_id: str,
    manager: Any,
    account_label: str,
) -> None:
    """Discover and persist the attachments Outlook copied server-side
    when ``createForward`` returned a new draft.

    Best-effort: the composer can still operate without the chips
    appearing immediately (a refresh after the user clicks Save reveals
    them via ``list_drafts``). We log on failure and swallow — the send
    path uses the provider's draft state directly, so the local rows
    are a UX nicety, not a correctness requirement.

    Persists each attachment row with ``blob=None``: the binary lives
    only in the provider draft (we never downloaded it). The Outlook
    send path will leave ``provider_attachment_id`` already set, so it
    skips re-uploading (D-27 partial-success contract).
    """
    try:
        attachments, _ = manager.list_message_attachments(
            account_label, provider_draft_id,
        )
    except Exception as exc:
        logger.warning(
            "Outlook createForward attachment inheritance discovery failed (%s): %s",
            type(exc).__name__, exc,
            exc_info=exc,
        )
        return

    if not attachments:
        return

    persisted_count = 0
    for meta in attachments:
        if meta.is_inline:
            # Inline images travel embedded inside the body — they are
            # not chips. Skip; the strict D-13 split applies to draft
            # attachments too.
            continue
        try:
            draft_attachment_store.insert(
                {
                    "draft_attachment_id": str(uuid.uuid4()),
                    "account_id": account_id,
                    "provider_draft_id": provider_draft_id,
                    "filename": meta.filename,
                    "mime_type": meta.mime_type,
                    "size": meta.size,
                    "content_id": meta.content_id,
                    "is_inline": False,
                    "blob": None,
                    "source_account_id": None,
                    "source_attachment_id": None,
                }
            )
            persisted_count += 1
        except DatabaseError as exc:
            logger.warning(
                "Outlook inherited attachment persist failed (%s): %s",
                type(exc).__name__, exc,
                exc_info=exc,
            )
        except Exception as exc:
            logger.warning(
                "Outlook inherited attachment unexpected persist error (%s): %s",
                type(exc).__name__, exc,
                exc_info=exc,
            )

    # Best-effort post-insert: stamp the provider_attachment_id so the
    # send path knows they are already at the provider.
    if persisted_count > 0:
        try:
            rows = draft_attachment_store.list_by_draft(account_id, provider_draft_id)
            pairs: list[tuple[str, str]] = []
            for row in rows:
                pid = None
                for meta in attachments:
                    if (
                        meta.filename == row.get("filename")
                        and not meta.is_inline
                        and meta.provider_attachment_id
                    ):
                        pid = meta.provider_attachment_id
                        break
                if pid:
                    pairs.append((row["draft_attachment_id"], pid))
            if pairs:
                draft_attachment_store.batch_update_provider_attachment_ids(pairs)
        except Exception as exc:
            logger.warning(
                "Outlook inherited attachment provider_id stamp failed (%s): %s",
                type(exc).__name__, exc,
                exc_info=exc,
            )


def update_draft(
    mailbox_id: str,
    account_id: str,
    provider_draft_id: str,
    payload: DraftUpdate,
    user_id: str,
) -> DraftOut:
    """
    Replace an existing draft at the provider and persist the new
    content locally. Provider-First: the provider call runs before any
    DB write, so the local row is only updated after the provider
    acknowledges the change.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    # Sanitise the rich-text HTML body ONCE at entry and reuse for both
    # the provider replacement and the persisted row (see create_draft).
    sanitized_body = sanitize_outbound_html(payload.body)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft update (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError(
            "Failed to look up account while updating draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft update."
        )

    try:
        existing_draft = draft_store.get(provider_draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during draft update (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError(
            "Failed to look up draft while updating it."
        ) from exc
    if existing_draft is None:
        raise DraftNotFound(
            f"Draft '{provider_draft_id}' not found for account "
            f"'{account_id}' during draft update."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(
                updated_tokens, label_lookup, fallback=DraftUpdateError,
            )
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftUpdateError,
        )

        try:
            manager.update_draft(
                account_label,
                provider_draft_id,
                payload.to_recipients,
                payload.cc_recipients,
                payload.bcc_recipients,
                payload.subject,
                sanitized_body,
            )
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftUpdateError,
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "provider_draft_id": provider_draft_id,
                },
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft update (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftUpdateError(
                "Unexpected failure while updating draft at provider."
            ) from exc

        row = {
            "provider_draft_id": provider_draft_id,
            "account_id": account_id,
            "to_recipients": list(payload.to_recipients),
            "cc_recipients": list(payload.cc_recipients),
            "bcc_recipients": list(payload.bcc_recipients),
            "subject": payload.subject,
            "body": sanitized_body,
        }
        try:
            persisted = draft_store.update(row)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB persist error during update (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftUpdateError(
                "Failed to persist draft to database after provider update."
            ) from exc

        return _draft_out_from_row(persisted)
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftUpdateError("Failed to update draft.") from exc


def delete_draft(
    mailbox_id: str,
    account_id: str,
    draft_id: str,
    user_id: str,
) -> dict[str, str]:
    """
    Delete a draft at the provider and then from the local database.
    Provider-First: only delete locally if the provider call succeeds.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        account = account_store.get(mailbox_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account lookup error during draft deletion (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError(
            "Failed to look up account while deleting draft."
        ) from exc
    if account is None:
        raise AccountNotFound(
            f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
            "during draft deletion."
        )

    try:
        existing = draft_store.get(draft_id, account_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected draft lookup error during draft deletion (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError(
            "Failed to look up draft for deletion."
        ) from exc
    if existing is None:
        raise DraftNotFound(
            f"Draft '{draft_id}' not found for account '{account_id}' "
            "during draft deletion."
        )

    try:
        provider = str(account.get("provider") or "").lower()
        account_label = f"{mailbox_id}__{account_id}"
        manager = build_manager_for_accounts([account])

        app_credentials = load_wrapped_app_credentials(provider)
        user_tokens = load_wrapped_account_tokens(mailbox_id, account_id, provider)
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            account_label: (app_credentials, user_tokens),
        }
        label_lookup: dict[str, tuple[str, str, str]] = {
            account_label: (mailbox_id, account_id, provider),
        }

        updated_tokens = manager.authenticate_all_silent(auth_payloads)
        if updated_tokens:
            _persist_refreshed_tokens(updated_tokens, label_lookup, fallback=DraftDeleteError)
        raise_on_silent_auth_errors(
            manager.get_last_errors(), fallback=DraftDeleteError,
        )

        try:
            manager.delete_draft(account_label, draft_id)
        except CoreError as exc:
            raise translate_core_error(
                exc,
                fallback=DraftDeleteError,
                context={"account_id": account_id, "draft_id": draft_id},
            ) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected error during provider draft deletion (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftDeleteError(
                "Unexpected failure while deleting draft at provider."
            ) from exc

        try:
            draft_store.delete(draft_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft DB delete error (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftDeleteError(
                "Failed to delete draft from database after provider deletion."
            ) from exc

        return {"status": "deleted"}
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "Unexpected draft deletion error (%s): %s",
            type(exc).__name__, exc,
        )
        raise DraftDeleteError("Failed to delete draft.") from exc


def list_drafts(
    mailbox_id: str,
    user_id: str,
    account_id: str | None = None,
) -> list[DraftOut]:
    """
    List drafts for a mailbox, optionally filtered to a single account.

    Pure DB read: does not contact any provider. Enforces mailbox ownership
    via ensure_mailbox_access.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during draft listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise DraftListError(
                "Failed to look up account for draft listing."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during draft listing."
            )

        try:
            rows = draft_store.list_by_account(account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft listing error for account '%s' (%s): %s",
                account_id, type(exc).__name__, exc,
            )
            raise DraftListError(
                "Unexpected failure while listing drafts by account."
            ) from exc
    else:
        try:
            rows = draft_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected draft listing error for mailbox '%s' (%s): %s",
                mailbox_id, type(exc).__name__, exc,
            )
            raise DraftListError(
                "Unexpected failure while listing drafts across mailbox."
            ) from exc

    return [_draft_out_from_row(row) for row in rows]
