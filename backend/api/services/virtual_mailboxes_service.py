"""
Service layer for virtual (fake) mailboxes.

A virtual mailbox is a user-defined filtered view over the messages
that already live in ``email_metadata``. This module owns:

- CRUD on the ``virtual_mailboxes`` table.
- Cross-checking that every ``account_id`` referenced by a virtual
  mailbox actually belongs to the requesting user.
- The translation from ``filter_payload`` to ``email_metadata``
  predicates at listing time.

It deliberately does NOT touch any provider — virtual mailboxes never
sync new emails, they only display the ones already pulled by the real
mailboxes underneath them. See ``docs/features/bandejas-ficticias.md``
for the behavioural contract.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    VirtualMailboxListError,
    VirtualMailboxNotFound,
    VirtualMailboxOperationError,
)
from api.schemas.email import EmailPageOut
from api.schemas.virtual_mailbox import (
    ALLOWED_FILTER_KEYS,
    VirtualMailboxCreate,
    VirtualMailboxOut,
    VirtualMailboxUpdate,
)
from api.services.services_helpers import (
    enrich_items_with_folders,
    parse_search_query,
    row_to_email_metadata_out,
    translate_database_error,
)
from database import (
    account_store,
    DatabaseError,
    email_metadata_store,
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


def _owned_account_ids(user_id: str) -> set[str]:
    """Return the set of every ``account_id`` the user owns across all of
    their real mailboxes.

    Single JOIN query (no N+1) — replaces the prior pattern of one
    ``mailbox_store.list_by_owner`` plus one ``list_by_mailbox`` per
    mailbox. Re-resolved on every CRUD and every read because the
    underlying catalogue is the source of truth: a revoked mailbox or
    a deleted account must not be exposed through a stale
    virtual-mailbox definition.
    """
    try:
        return set(account_store.list_account_ids_by_user(user_id))
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected error listing owned account_ids during virtual mailbox account resolution (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxOperationError(
            "Failed to list owned accounts while resolving virtual mailbox accounts."
        ) from exc


def _validate_account_ids_owned_by_user(
    account_ids: list[str],
    user_id: str,
) -> None:
    """Reject the payload if any requested ``account_id`` is not owned by
    *user_id*.

    Pydantic only validates the shape of ``account_ids``; this is where
    we resolve the requested ids against the database and confirm the
    caller actually owns them. Without this step a malicious caller
    could create a virtual mailbox that aggregates someone else's
    account ids and read every message they own.
    """
    owned = _owned_account_ids(user_id)
    for requested in account_ids:
        if requested not in owned:
            raise AccountNotFound(
                f"Account '{requested}' not found in any of your mailboxes "
                "while validating virtual mailbox accounts."
            )


def create_virtual_mailbox(
    user_id: str,
    payload: VirtualMailboxCreate,
) -> VirtualMailboxOut:
    """Insert a new virtual mailbox owned by *user_id*."""
    _validate_account_ids_owned_by_user(payload.account_ids, user_id)
    row_input: dict[str, Any] = {
        "virtual_mailbox_id": str(uuid.uuid4()),
        "owner_user_id": user_id,
        "display_name": payload.display_name.strip(),
        "account_ids": list(payload.account_ids),
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
    _validate_account_ids_owned_by_user(payload.account_ids, user_id)
    row_input: dict[str, Any] = {
        "virtual_mailbox_id": virtual_mailbox_id,
        "display_name": payload.display_name.strip(),
        "account_ids": list(payload.account_ids),
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


def _filter_account_ids_still_owned(
    persisted: list[str],
    user_id: str,
) -> list[str]:
    """Return only the ``account_ids`` from *persisted* that the user still
    owns.

    The persisted list is a snapshot taken at create/update time; the
    catalogue may have changed since (account disconnected, mailbox
    deleted). Filtering on every read keeps the virtual mailbox alive
    with the surviving subset instead of 404'ing — the "definition not
    collection" contract.
    """
    if not persisted:
        return []
    owned = _owned_account_ids(user_id)
    return [aid for aid in persisted if aid in owned]


def _build_filter_args(filter_payload: dict[str, Any]) -> tuple[
    str | None, list[str] | None, dict[str, Any],
]:
    """Translate ``filter_payload`` into args for ``list_filtered``.

    Returns ``(box, box_not_in, extra_filters)``. Box selection follows
    the same default-exclude rule as the favourites view (and the spec
    for fake mailboxes): when the user did not set a ``box`` explicitly,
    TRASH, SPAM and ARCHIVE are excluded unless an explicit ``box_not_in``
    override is provided. ARCHIVE is excluded only by DEFAULT (not forced
    onto an override nor the ``box_not_in: []`` opt-in) — archived mail
    stays out of a fake mailbox by default but is rescued by ``in:archive``
    in the listing.

    ``DELETED`` is excluded on TOP of that rule in every ``box_not_in``
    branch (default, custom override, and the ``box_not_in: []`` opt-in):
    it is a local hard-delete state with no ``FilterBox`` membership, so
    a user can never ask to see it, and the negative ``box_not_in``
    predicate would otherwise leak it (the positive ``box`` predicate
    cannot, since ``box = X`` never matches ``DELETED``).
    """
    extra_filters: dict[str, Any] = {}
    box: str | None = None
    box_not_in: list[str] | None = None

    if not isinstance(filter_payload, dict):
        return None, ["TRASH", "SPAM", "ARCHIVE", "DELETED"], {}

    box_value = filter_payload.get("box")
    box_not_in_value = filter_payload.get("box_not_in")
    if box_value:
        box = str(box_value)
    elif box_not_in_value is not None:
        # ``[]`` means "do not exclude anything" — caller explicitly asks
        # to include TRASH/SPAM (and ARCHIVE). Truthiness would collapse the
        # empty list to the default exclusion and reverse the intent.
        box_not_in = [str(v) for v in box_not_in_value]
    else:
        # ``ARCHIVE`` joins TRASH/SPAM in the DEFAULT exclusion only — like
        # them, archived mail stays out of a fake mailbox by default. Unlike
        # ``DELETED`` (appended to EVERY branch below), it is NOT forced onto
        # the ``box_not_in: []`` opt-in nor onto a non-empty override: a caller
        # asking to see everything, or pinning its own exclusion list, is
        # honoured literally. ARCHIVE stays reachable via ``in:archive`` (the
        # rescue in ``list_emails_for_virtual_mailbox``).
        box_not_in = ["TRASH", "SPAM", "ARCHIVE"]

    # ``DELETED`` is a local hard-delete state with NO product surface (it is
    # not a member of ``FilterBox``), so it must never appear in a virtual
    # mailbox regardless of the box filter. The positive-box branch already
    # excludes it by construction (``AND box = X``); here we guarantee it for
    # every ``box_not_in`` branch — default, custom override, and the
    # ``box_not_in: []`` opt-in alike. The ``not in`` guard keeps the result
    # idempotent if a caller ever pre-includes ``DELETED`` in the list.
    if box_not_in is not None and "DELETED" not in box_not_in:
        box_not_in.append("DELETED")

    for key in ALLOWED_FILTER_KEYS - {"box", "box_not_in"}:
        if key in filter_payload and filter_payload[key] is not None:
            extra_filters[key] = filter_payload[key]

    return box, box_not_in, extra_filters


def list_emails_for_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> EmailPageOut:
    """Return a page of the filtered listing produced by a virtual mailbox.

    The virtual listing ALWAYS groups by conversation (conversation view):
    ``group_by_thread=True`` collapses each thread into its most-recent
    message and ``total`` counts threads, not messages. Combined with
    ``distinct_provider_message_id=True``, the same provider message
    surfaced under two ``account_id`` rows (a vmbox can aggregate accounts
    across different real mailboxes) is deduplicated in SQL BEFORE grouping
    and BEFORE ``LIMIT``/``OFFSET`` — so a page is never short by a
    duplicate, threads are never split across pages, and ``total`` is the
    correct deduplicated thread count.
    """
    record = _load_owned_virtual_mailbox(virtual_mailbox_id, user_id)

    persisted = [str(a) for a in (record.get("account_ids") or [])]
    account_ids = _filter_account_ids_still_owned(persisted, user_id)
    if not account_ids:
        return EmailPageOut(items=[], total=0, limit=limit, offset=offset)

    box, box_not_in, extra_filters = _build_filter_args(record.get("filter_payload") or {})

    # ``parse_search_query`` is a pure string operation (no DB) computed
    # once and shared by BOTH calls below so the deduplicated count
    # matches the deduplicated page. Two separate try blocks keep the
    # failure messages unique per raise site (API CLAUDE.md §7).
    parsed = parse_search_query(q)
    tokens = parsed.tokens
    operator_clauses = parsed.operator_clauses

    # ``in:`` INTERSECTS (AND) with the box the fake mailbox already
    # defines, instead of overriding it like the regular listing does.
    # Asking for a box the fake mailbox excludes yields an empty page
    # naturally — surfaced via the same short-circuit used for the
    # "no owned accounts" case, so we never pass both ``box`` and
    # ``box_not_in`` to the repository (mixing them is a programming
    # error there).
    if parsed.box_override is not None:
        ov = parsed.box_override
        if box is not None:
            # Fake mailbox pinned to a single box: only the same box is
            # compatible; any other ``in:`` collapses to empty.
            if ov != box:
                return EmailPageOut(items=[], total=0, limit=limit, offset=offset)
        elif box_not_in:
            # Fake mailbox carries exclusions: the default
            # ``["TRASH","SPAM","ARCHIVE","DELETED"]``, an explicit non-empty
            # list (always with ``DELETED`` appended), or the ``box_not_in: []``
            # opt-in which ``_build_filter_args`` returns as ``["DELETED"]``.
            # ``in:`` of an excluded box is normally empty; otherwise it
            # narrows to that single box. ``DELETED`` is never a legal ``in:``
            # value (no ``in:deleted``; ``in:trash`` maps to ``TRASH``), so the
            # ever-present ``DELETED`` entry can never block a legitimate
            # ``in:`` here.
            #
            # ``ARCHIVE`` is the ONE exception: it is excluded by default (so it
            # is in ``box_not_in``), yet — unlike TRASH/SPAM/DELETED — an
            # explicit ``in:archive`` is allowed to RESCUE it, because the
            # product requires archived mail to be reachable inside a fake
            # mailbox via the lupa. It is the only default-excluded box that is
            # also a legal ``in:`` value, so it is the only one we narrow
            # instead of short-circuiting. ``in:trash`` / ``in:spam`` over a
            # default fake mailbox still collapse to an empty page.
            if ov == "ARCHIVE":
                box = ov
                box_not_in = None
            elif ov in box_not_in:
                return EmailPageOut(items=[], total=0, limit=limit, offset=offset)
            else:
                box = ov
                box_not_in = None
        else:
            # ``box`` is None AND ``box_not_in`` is falsy. After the DELETED
            # sanitisation in ``_build_filter_args`` this branch is no longer
            # reachable from that helper (every ``box_not_in`` path now
            # carries at least ``["DELETED"]``); kept as a defensive fallback
            # so a future caller passing an empty exclusion still narrows the
            # listing to the requested ``in:`` box instead of mis-handling it.
            box = ov
            box_not_in = None

    try:
        rows = email_metadata_store.list_filtered(
            account_ids, box, tokens, limit, offset,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            operator_clauses=operator_clauses or None,
            distinct_provider_message_id=True,
            group_by_thread=True,
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

    try:
        total = email_metadata_store.count_filtered(
            account_ids, box, tokens,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            operator_clauses=operator_clauses or None,
            distinct_provider_message_id=True,
            group_by_thread=True,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected virtual mailbox email count error (%s): %s",
            type(exc).__name__, exc,
        )
        raise VirtualMailboxListError(
            "Failed to count emails while paginating the virtual mailbox listing."
        ) from exc

    items = [row_to_email_metadata_out(row) for row in rows]
    enrich_items_with_folders(items)  # folder chips (best-effort, single batch)
    return EmailPageOut(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
