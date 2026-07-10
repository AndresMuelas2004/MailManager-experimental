"""Vista de conversacion (lectura + sync perezoso): hilo completo desde el proveedor y su persistencia."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    ConversationFetchError,
    EmailNotFound,
)
from core.email import (
    ConversationMessage,
    CoreError,
    EmailMetadata,
)
from api.schemas.email import (
    ConversationOut,
    EmailMetadataOut,
)
from api.services.services_helpers import (
    build_manager_for_accounts,
    ensure_mailbox_access,
    persist_email_metadata_batch,
    raise_on_silent_auth_errors,
    row_to_email_metadata_out,
    translate_core_error,
    translate_database_error,
)
from database import (
    account_store,
    email_metadata_store,
    DatabaseError,
)

from ._comunes import _build_auth_context, _persist_refreshed_tokens


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
