"""
Service layer for virtual (fake) mailboxes.

A virtual mailbox is a user-defined filtered view over the messages
that already live in ``email_metadata``. This module owns:

- CRUD on the ``virtual_mailboxes`` table.
- The translation from ``scope_payload`` + ``filter_payload`` to
  ``email_metadata`` predicates at listing time.

It deliberately does NOT touch any provider — virtual mailboxes never
sync new emails, they only display the ones already pulled by the real
mailboxes underneath them. See ``Ignore/bandejas-ficticias.md`` for the
behavioural contract.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    ApiError,
    Forbidden,
    MailboxLookupError,
    MailboxNotFound,
    VirtualMailboxInvalid,
    VirtualMailboxListError,
    VirtualMailboxNotFound,
    VirtualMailboxOperationError,
)
from api.schemas.email import EmailMetadataOut
from api.schemas.virtual_mailbox import (
    ALLOWED_FILTER_KEYS,
    VirtualMailboxCreate,
    VirtualMailboxOut,
    VirtualMailboxUpdate,
)
from api.services.services_helpers import (
    parse_search_tokens,
    row_to_email_metadata_out,
    translate_database_error,
)
from database import (
    account_store,
    DatabaseError,
    email_metadata_store,
    mailbox_store,
    virtual_mailbox_store,
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _load_owned_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Load a virtual mailbox by id, enforcing ownership.

    Mirrors :py:func:`ensure_mailbox_access` semantics: a foreign row
    surfaces as 404, not 403, to avoid leaking existence via the
    distinct error.
    """
    try:
        record = virtual_mailbox_store.get(virtual_mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox lookup error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to look up virtual mailbox."
        ) from exc
    if record is None or str(record.get("owner_user_id")) != str(user_id):
        raise VirtualMailboxNotFound(
            f"Virtual mailbox '{virtual_mailbox_id}' not found."
        )
    return record


def _validate_scope_against_ownership(
    payload: VirtualMailboxCreate | VirtualMailboxUpdate,
    user_id: str,
) -> None:
    """Cross-check that the referenced mailboxes / accounts belong to *user_id*.

    Pydantic only validates the SHAPE of ``scope_payload``; this is
    where we resolve the referenced resources against the database and
    confirm the requesting user actually owns them. Without this step
    a malicious caller could create a virtual mailbox that aggregates
    someone else's account ids and read every message they own.
    """
    scope_kind = payload.scope_kind
    sp = payload.scope_payload

    if scope_kind == "mailbox":
        try:
            mailbox = mailbox_store.get(sp.mailbox_id or "")
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected mailbox lookup error during virtual mailbox validation (%s): %s",
                type(exc).__name__, exc,
            )
            raise MailboxLookupError("Failed to look up scoped mailbox.") from exc
        if mailbox is None:
            raise MailboxNotFound(
                f"Mailbox '{sp.mailbox_id}' not found while validating "
                "virtual mailbox scope."
            )
        if str(mailbox.get("owner_user_id")) != str(user_id):
            raise Forbidden(
                "You do not have access to the mailbox referenced by the virtual mailbox scope."
            )
        return

    if scope_kind == "accounts":
        try:
            user_mailboxes = mailbox_store.list_by_owner(user_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected mailbox listing error during virtual mailbox validation (%s): %s",
                type(exc).__name__, exc,
            )
            raise MailboxLookupError(
                "Failed to list user mailboxes while validating virtual mailbox scope."
            ) from exc
        owned_account_ids: set[str] = set()
        for mailbox in user_mailboxes:
            mid = str(mailbox.get("mailbox_id") or "")
            if not mid:
                continue
            try:
                accounts = account_store.list_by_mailbox(mid)
            except DatabaseError as exc:
                raise translate_database_error(exc) from exc
            except Exception as exc:
                logger.warning(
                    "Unexpected account listing error during virtual mailbox validation (%s): %s",
                    type(exc).__name__, exc,
                )
                raise VirtualMailboxOperationError(
                    "Failed to list accounts while validating virtual mailbox scope."
                ) from exc
            for account in accounts:
                aid = str(account.get("account_id") or "")
                if aid:
                    owned_account_ids.add(aid)
        for requested in sp.account_ids or []:
            if requested not in owned_account_ids:
                raise AccountNotFound(
                    f"Account '{requested}' not found in any of your mailboxes "
                    "while validating virtual mailbox scope."
                )
        return

    # scope_kind == "all" — nothing to cross-check.


def create_virtual_mailbox(
    user_id: str,
    payload: VirtualMailboxCreate,
) -> VirtualMailboxOut:
    """Insert a new virtual mailbox owned by *user_id*."""
    _validate_scope_against_ownership(payload, user_id)
    row_input: dict[str, Any] = {
        "virtual_mailbox_id": str(uuid.uuid4()),
        "owner_user_id": user_id,
        "display_name": payload.display_name.strip(),
        "scope_kind": payload.scope_kind,
        "scope_payload": payload.scope_payload.model_dump(exclude_none=True),
        "filter_payload": payload.filter_payload.model_dump(exclude_none=True),
    }
    try:
        record = virtual_mailbox_store.create(row_input)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox create error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to create virtual mailbox."
        ) from exc
    return VirtualMailboxOut(**record)


def list_virtual_mailboxes(user_id: str) -> list[VirtualMailboxOut]:
    """Return every virtual mailbox owned by *user_id*."""
    try:
        rows = virtual_mailbox_store.list_by_owner(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to list virtual mailboxes."
        ) from exc
    return [VirtualMailboxOut(**row) for row in rows]


def get_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str,
) -> VirtualMailboxOut:
    """Return a single virtual mailbox owned by *user_id*."""
    record = _load_owned_virtual_mailbox(virtual_mailbox_id, user_id)
    return VirtualMailboxOut(**record)


def update_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str,
    payload: VirtualMailboxUpdate,
) -> VirtualMailboxOut:
    """Full-field replace of a virtual mailbox's definition."""
    _load_owned_virtual_mailbox(virtual_mailbox_id, user_id)
    _validate_scope_against_ownership(payload, user_id)
    row_input: dict[str, Any] = {
        "virtual_mailbox_id": virtual_mailbox_id,
        "display_name": payload.display_name.strip(),
        "scope_kind": payload.scope_kind,
        "scope_payload": payload.scope_payload.model_dump(exclude_none=True),
        "filter_payload": payload.filter_payload.model_dump(exclude_none=True),
    }
    try:
        record = virtual_mailbox_store.update(row_input)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox update error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to update virtual mailbox."
        ) from exc
    if record is None:
        # Race: the vmbox passed the ownership pre-check but was deleted
        # before the UPDATE landed. Surface as 404 instead of a generic
        # 500 — keeps the API surface coherent with GET/DELETE which
        # also return 404 when the row is gone.
        raise VirtualMailboxNotFound(
            f"Virtual mailbox '{virtual_mailbox_id}' disappeared during update."
        )
    return VirtualMailboxOut(**record)


def delete_virtual_mailbox(virtual_mailbox_id: str, user_id: str) -> None:
    """Delete a virtual mailbox owned by *user_id*."""
    _load_owned_virtual_mailbox(virtual_mailbox_id, user_id)
    try:
        deleted = virtual_mailbox_store.delete(virtual_mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox delete error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to delete virtual mailbox."
        ) from exc
    if not deleted:
        # Race: row vanished between the ownership pre-check and the
        # DELETE. Surface as 404 to mirror the GET/UPDATE contract — a
        # silent 200 would let two concurrent deletes both report
        # success, hiding the conflict from the caller.
        raise VirtualMailboxNotFound(
            f"Virtual mailbox '{virtual_mailbox_id}' disappeared before delete."
        )


# ---------------------------------------------------------------------------
# Listing emails for a virtual mailbox
# ---------------------------------------------------------------------------


def _resolve_scope_account_ids(
    record: dict[str, Any],
    user_id: str,
) -> list[str]:
    """Translate ``scope_kind`` + ``scope_payload`` into a list of account ids.

    Always re-validates ownership at read time — the user might have
    revoked a mailbox between create and now, and we must never expose
    foreign emails through a stale virtual mailbox. Missing referenced
    resources collapse to an empty result (the virtual mailbox stays
    valid but the listing is empty) instead of raising — the user gets
    a working but empty view, mirroring the "definition not collection"
    contract.
    """
    scope_kind = str(record.get("scope_kind") or "")
    scope_payload = record.get("scope_payload") or {}

    if scope_kind == "mailbox":
        scope_mailbox_id = str(scope_payload.get("mailbox_id") or "")
        if not scope_mailbox_id:
            return []
        try:
            mailbox = mailbox_store.get(scope_mailbox_id)
            if mailbox is None or str(mailbox.get("owner_user_id")) != str(user_id):
                return []
            accounts = account_store.list_by_mailbox(scope_mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected scope=mailbox resolution error during virtual mailbox listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise VirtualMailboxListError(
                "Failed to resolve scope=mailbox account ids for virtual mailbox listing."
            ) from exc
        return [str(a["account_id"]) for a in accounts]

    if scope_kind == "all":
        try:
            user_mailboxes = mailbox_store.list_by_owner(user_id)
            account_ids: list[str] = []
            for mailbox in user_mailboxes:
                mid = str(mailbox.get("mailbox_id") or "")
                if not mid:
                    continue
                accounts = account_store.list_by_mailbox(mid)
                account_ids.extend(str(a["account_id"]) for a in accounts)
            return account_ids
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected scope=all resolution error during virtual mailbox listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise VirtualMailboxListError(
                "Failed to resolve scope=all account ids for virtual mailbox listing."
            ) from exc

    if scope_kind == "accounts":
        requested = [str(aid) for aid in (scope_payload.get("account_ids") or [])]
        if not requested:
            return []
        try:
            user_mailboxes = mailbox_store.list_by_owner(user_id)
            allowed: set[str] = set()
            for mailbox in user_mailboxes:
                mid = str(mailbox.get("mailbox_id") or "")
                if not mid:
                    continue
                accounts = account_store.list_by_mailbox(mid)
                allowed.update(str(a["account_id"]) for a in accounts)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected scope=accounts resolution error during virtual mailbox listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise VirtualMailboxListError(
                "Failed to resolve scope=accounts account ids for virtual mailbox listing."
            ) from exc
        return [aid for aid in requested if aid in allowed]

    return []


def _build_filter_args(filter_payload: dict[str, Any]) -> tuple[
    str | None, list[str] | None, dict[str, Any],
]:
    """Translate ``filter_payload`` into args for ``list_filtered``.

    Returns ``(box, box_not_in, extra_filters)``. Box selection follows
    the same default-exclude-trash-and-spam rule as the favourites
    view (and the spec for fake mailboxes): when the user did not set
    a ``box`` explicitly, TRASH and SPAM are excluded unless an
    explicit ``box_not_in`` override is provided.
    """
    extra_filters: dict[str, Any] = {}
    box: str | None = None
    box_not_in: list[str] | None = None

    if not isinstance(filter_payload, dict):
        return None, ["TRASH", "SPAM"], {}

    box_value = filter_payload.get("box")
    box_not_in_value = filter_payload.get("box_not_in")
    if box_value:
        box = str(box_value)
    elif box_not_in_value is not None:
        # ``[]`` means "do not exclude anything" — caller explicitly asks
        # to include TRASH/SPAM. Truthiness would collapse the empty list
        # to the default exclusion and reverse the intent.
        box_not_in = [str(v) for v in box_not_in_value]
    else:
        box_not_in = ["TRASH", "SPAM"]

    for key in ALLOWED_FILTER_KEYS - {"box", "box_not_in"}:
        if key in filter_payload and filter_payload[key] is not None:
            extra_filters[key] = filter_payload[key]

    return box, box_not_in, extra_filters


def _dedupe_rows_by_provider_message_id(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse rows sharing a ``provider_message_id``, preferring the most
    complete one.

    The same Gmail / Outlook account can be connected as two distinct
    ``account_id`` rows under two different mailboxes — both syncs land
    the same provider message twice in ``email_metadata`` (PK is
    ``(account_id, provider_message_id)``). For ``scope='all'`` and
    ``scope='accounts'`` virtual mailboxes that aggregate across both
    accounts, the listing surfaces each message twice. A virtual mailbox
    is a "definition, not a collection" — collapsing the duplicates is a
    presentation decision that lives here, not in the shared SQL query
    (which is reused verbatim by regular box listings where the
    duplication is not possible because each listing is scoped to a
    single mailbox).

    Preference: pick the row whose ``to_email`` is a non-empty string
    (synced after migration 0031 with a real ``To`` header). Other rows
    can carry ``NULL`` (column was nullable before migration 0031 on
    older databases), ``''`` (migration default for missing ``To``
    headers), or a real address — only the last case scores as
    "populated". ``to_name`` is used as a secondary signal to handle the
    rare case where both rows have empty ``to_email`` but only one has
    a populated ``to_name``. Among rows that tie on both signals,
    pick the most recent ``received_at``.

    Implementation does an explicit pairwise pick rather than a
    sort+first-wins dict insertion to avoid any subtle interaction
    with Python's stable sort on equal keys: the surviving row is
    chosen by a deterministic comparison against the current best.
    """

    def _completeness_score(row: dict[str, Any]) -> tuple[int, int, float]:
        # Higher tuples win. Components, most significant first:
        #   1. to_email is a real address (non-empty string after strip).
        #   2. to_name is populated (secondary, correlates with #1).
        #   3. received_at most recent (tie-breaker — last writer wins).
        to_email = row.get("to_email")
        to_email_score = 1 if (isinstance(to_email, str) and to_email.strip()) else 0
        to_name = row.get("to_name")
        to_name_score = 1 if (isinstance(to_name, str) and to_name.strip()) else 0
        ts = row.get("received_at")
        ts_seconds = ts.timestamp() if ts is not None else 0.0
        return (to_email_score, to_name_score, ts_seconds)

    chosen: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("provider_message_id")
        if key is None:
            continue
        current = chosen.get(key)
        if current is None or _completeness_score(row) > _completeness_score(current):
            chosen[key] = row

    # Restore the chronological-DESC order the SQL produced — the
    # dict iteration order reflects insertion order from the input,
    # and the frontend expects newest-first listings.
    return sorted(
        chosen.values(),
        key=lambda r: r.get("received_at") or 0,
        reverse=True,
    )


def list_emails_for_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[EmailMetadataOut]:
    """Return the filtered listing produced by a virtual mailbox."""
    record = _load_owned_virtual_mailbox(virtual_mailbox_id, user_id)

    account_ids = _resolve_scope_account_ids(record, user_id)
    if not account_ids:
        return []

    box, box_not_in, extra_filters = _build_filter_args(record.get("filter_payload") or {})

    try:
        tokens = parse_search_tokens(q)
        rows = email_metadata_store.list_filtered(
            account_ids, box, tokens, limit, offset,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox email listing error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxListError(
            "Failed to list emails for virtual mailbox."
        ) from exc

    rows = _dedupe_rows_by_provider_message_id(rows)
    return [row_to_email_metadata_out(row) for row in rows]
