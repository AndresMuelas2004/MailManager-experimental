"""Vista de conversacion (lectura + sync perezoso): hilo completo desde el proveedor y su persistencia."""

from __future__ import annotations

import logging
from typing import Any

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
    load_thread_metadata,
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
    message: ConversationMessage,
    account_id: str,
    *,
    provider_message_id: str | None = None,
) -> EmailMetadata:
    """Convert a provider ``ConversationMessage`` into a syncable
    ``EmailMetadata`` — carries ``is_favorite`` through (the metadata upsert
    now persists it; dropping it here would un-favourite the row) and stamps
    ``account_id`` like the sync path.

    ``provider_message_id`` overrides the message's own id when the lazy-sync
    reconciled it to the stable id already stored for the same physical
    message (Outlook hands the same message different ids per endpoint —
    see ``_build_id_remap``). ``None`` keeps the message's own id (Gmail, and
    genuinely-new Outlook messages with no stored twin)."""
    return EmailMetadata(
        provider_message_id=provider_message_id or message.provider_message_id,
        thread_id=message.thread_id,
        from_email=message.from_email,
        from_name=message.from_name,
        subject=message.subject,
        received_at=message.received_at,
        is_read=message.is_read,
        box=message.box,
        is_favorite=message.is_favorite,
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
        # — the viewer must open even if the cache-fill fails. ``thread_id`` is
        # the authoritative thread of the clicked (base) row; the lazy-sync
        # reconciles each fetched member against the rows already stored under
        # it so Outlook's per-endpoint id drift does not duplicate rows.
        _lazy_sync_conversation(account_id, thread_id, members)

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


def _physical_message_identity(
    received_at: Any, from_email: str | None, subject: str | None,
) -> tuple[Any, str, str]:
    """Endpoint-independent identity of a physical message within a thread.

    Outlook returns a DIFFERENT REST id for the same physical message on the
    folder-delta endpoint (what sync stored) vs the mailbox-wide
    ``$filter=conversationId`` endpoint (what ``fetch_conversation`` returns),
    and ``Prefer: IdType="ImmutableId"`` does NOT reconcile the two (verified
    live — external-apis-used/Outlook/08). These three fields are parsed
    identically on both endpoints (both go through ``_parse_graph_message``),
    so together they identify the same physical message across them.
    ``received_at`` (a tz-aware datetime; equal instants hash equal even from
    different tzinfo) is the real discriminator within a thread — two distinct
    messages differ by send time — and ``from_email`` + ``subject`` harden it.
    """
    return (
        received_at,
        (from_email or "").strip().lower(),
        (subject or "").strip(),
    )


def _build_id_remap(
    existing_rows: list[dict[str, Any]],
    members: list[ConversationMessage],
) -> dict[str, str]:
    """Map a fetched member's provider id to the stable stored id of the same
    physical message, for members whose OWN id is not already stored.

    ``existing_rows`` are ``list_metadata_by_thread``'s rows, ordered
    ``received_at DESC, provider_message_id``, so ``setdefault`` deterministically
    keeps — when past opens left duplicate rows for one physical message — the
    SAME representative id the grouped listing picks (received_at DESC, then min
    provider_message_id). That is the id the frontend requests content under, so
    folding new opens onto it makes reopening a cache hit.

    A member already stored under its own id (every Gmail member — Gmail ids are
    stable across endpoints; and an already-reconciled Outlook row) is left
    untouched, so Gmail is a strict no-op and only new/unstable Outlook ids are
    rewritten. Returns only the entries that need remapping.
    """
    stored_ids = {r["provider_message_id"] for r in existing_rows}
    identity_to_id: dict[tuple[Any, str, str], str] = {}
    for row in existing_rows:
        identity_to_id.setdefault(
            _physical_message_identity(
                row["received_at"], row.get("from_email"), row.get("subject"),
            ),
            row["provider_message_id"],
        )
    remap: dict[str, str] = {}
    for m in members:
        if m.provider_message_id in stored_ids:
            continue
        stable = identity_to_id.get(
            _physical_message_identity(m.received_at, m.from_email, m.subject),
        )
        if stable and stable != m.provider_message_id:
            remap[m.provider_message_id] = stable
    return remap


def _reconcile_thread_ids(
    account_id: str, thread_id: str, members: list[ConversationMessage],
) -> dict[str, str]:
    """Best-effort id-remap for the lazy-sync (see ``_build_id_remap``).

    Reads the thread's stored rows and returns the member→stable-id remap.
    Any read failure returns ``{}`` (logged) so the lazy-sync degrades to
    persisting the members verbatim — no worse than before — instead of
    aborting the viewer.
    """
    try:
        existing_rows = load_thread_metadata(
            account_id, thread_id, fallback=ConversationFetchError,
        )
        return _build_id_remap(existing_rows, members)
    except Exception as exc:
        logger.warning(
            "Conversation lazy-sync id reconciliation failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
            exc_info=exc,
        )
        return {}


def _lazy_sync_conversation(
    account_id: str, thread_id: str, members: list[ConversationMessage],
) -> None:
    """Best-effort persistence of a fetched conversation's messages.

    Upserts every message into ``email_metadata`` (inserting the ones never
    synced, refreshing ``is_read`` / ``box`` / ``is_favorite`` / ``to_*`` of
    existing ones — the shared upsert still never touches ``thread_id`` /
    ``has_attachments``). ``is_favorite`` is now provider-authoritative on
    each open (both directions), so no separate favourite re-apply is needed.
    Swallowed on failure (logged) so a cache-fill hiccup never aborts the
    viewer.

    Each member's provider id is first reconciled against the ids already
    stored for ``thread_id`` (``_reconcile_thread_ids``): Outlook returns a
    non-deterministic id for the same physical message on the conversation
    endpoint, so persisting members verbatim INSERTs a duplicate row per open
    (inflating ``thread_message_count`` and leaving the grouped-listing
    representative — hence the cached body — under an unstable id). Remapping
    onto the stored stable id turns those inserts into UPDATEs. Gmail ids are
    stable, so the remap is empty and behaviour is unchanged.

    NOTE: this only affects the LISTING's thread row on the next list; it
    does NOT change the ``ConversationOut`` of this call (the viewer reads
    per-message state from the provider members, not from the DB).
    """
    if not members:
        return
    id_remap = _reconcile_thread_ids(account_id, thread_id, members)
    # Dedupe by the FINAL id, keeping the newest member for a key (members
    # arrive oldest-first): two members can only collapse to one id in the rare
    # physical-key collision, and the batch upsert rejects the same conflict key
    # twice ("cannot affect row a second time").
    by_id: dict[str, EmailMetadata] = {}
    for m in members:
        pmid = id_remap.get(m.provider_message_id, m.provider_message_id)
        by_id[pmid] = _conversation_message_to_metadata(
            m, account_id, provider_message_id=pmid,
        )
    try:
        persist_email_metadata_batch(
            account_id, list(by_id.values()), fallback=ConversationFetchError,
        )
    except Exception as exc:
        logger.warning(
            "Conversation lazy-sync metadata persist failed for account '%s' (%s): %s",
            account_id, type(exc).__name__, exc,
            exc_info=exc,
        )
        return
