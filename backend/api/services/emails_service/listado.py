"""Listado paginado de correos y recuento de no leidos (100% local, sin llamadas al proveedor)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api.errors.exceptions import (
    AccountNotFound,
    EmailListError,
    UnreadCountError,
)
from api.schemas.email import (
    AccountUnreadDetail,
    EmailPageOut,
    UnreadCountOut,
)
from api.services.services_helpers import (
    ensure_mailbox_access,
    parse_search_query,
    row_to_email_metadata_out,
    translate_database_error,
)
from database import (
    account_store,
    email_metadata_store,
    DatabaseError,
)


def list_emails(
    mailbox_id: str,
    box: str,
    user_id: str,
    account_id: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    favorite: bool | None = None,
    group_by_thread: bool = False,
    *,
    sort: str = "date",
    sort_dir: str = "desc",
    unread: bool = False,
    has_attachment: bool = False,
    favorite_only: bool = False,
) -> EmailPageOut:
    """List a page of email metadata for a mailbox, with the exact total.

    Returns an :class:`EmailPageOut` envelope: the requested page of
    rows plus ``total`` — the count of the WHOLE filtered set (same
    ``box`` / ``q`` / ``favorite`` / chips / accounts), used by the
    frontend to render numbered pagination. ``total`` reflects only the
    locally synced copy, never the provider's live mailbox size.

    When ``favorite=True``, the listing only returns favourite messages
    and TRASH / SPAM are excluded by default (matching the dedicated
    Favourites view documented in ``Ignore/Favoritos-Funcionalidad.md``).

    ``sort`` / ``sort_dir`` choose the ordering (``date`` / ``sender`` /
    ``subject`` × ``asc`` / ``desc``); the defaults (``date`` / ``desc``)
    reproduce the historical fixed ordering. The quick-filter chips
    (``unread`` / ``has_attachment`` / ``favorite_only``) are plain AND
    filters on the current box, translated into the SAME operator clauses
    the lupa uses (``is:unread`` / ``has:attachment`` / ``is:favorite``);
    they combine with each other, with any ``q`` operators, and with free
    text. The chips are keyword-only with defaults that reproduce the
    pre-feature behaviour, so existing positional callers are unaffected.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    if account_id is not None:
        try:
            account = account_store.get(mailbox_id, account_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account lookup error during email listing (%s): %s",
                type(exc).__name__, exc,
            )
            raise EmailListError(
                "Failed to look up account for email listing."
            ) from exc
        if account is None:
            raise AccountNotFound(
                f"Account '{account_id}' not found in mailbox '{mailbox_id}' "
                "during email listing."
            )
        account_ids: list[str] = [account_id]
    else:
        try:
            accounts = account_store.list_by_mailbox(mailbox_id)
        except DatabaseError as exc:
            raise translate_database_error(exc) from exc
        except Exception as exc:
            logger.warning(
                "Unexpected account listing error during email listing for mailbox '%s' (%s): %s",
                mailbox_id, type(exc).__name__, exc,
            )
            raise EmailListError(
                "Failed to load mailbox accounts for email listing."
            ) from exc
        account_ids = [str(a["account_id"]) for a in accounts]
        if not account_ids:
            return EmailPageOut(items=[], total=0, limit=limit, offset=offset)

    # ``parse_search_query`` is a pure string operation (no DB) computed
    # once and shared by BOTH calls below, so ``count_filtered`` counts
    # EXACTLY the set ``list_filtered`` lists (same box / tokens /
    # extra_filters / box_not_in / operator_clauses). Two separate try
    # blocks keep the failure messages unique per raise site (API
    # CLAUDE.md §7).
    parsed = parse_search_query(q)
    tokens = parsed.tokens
    # Copy to a fresh list before appending: ``ParsedSearchQuery`` is a
    # frozen dataclass, but ``frozen=True`` only blocks reassigning the
    # attribute — the list object it points to is still mutable, so
    # appending to ``parsed.operator_clauses`` directly would mutate the
    # dataclass's internal list in place. The copy keeps the parsed result
    # pristine and disjoint from the chip clauses we add below.
    operator_clauses = list(parsed.operator_clauses)

    # Quick-filter chips → reuse the SAME operator-clause builders the lupa
    # uses (``is_read_op`` / ``has_attachments`` / ``is_favorite_op`` are
    # already in ``_OPERATOR_CLAUSE_BUILDERS``); no new SQL surface. They
    # AND with any ``q``-derived operator clauses and with each other, and
    # are independent of the ``favorite`` anchor param below (a chip filters
    # the current box; the anchor powers the dedicated Favourites view).
    # Appending after the lupa clauses continues the ``op{idx}`` numbering
    # without collision (same mechanics as a repeated ``from:`` operator).
    if unread:
        operator_clauses.append(("is_read_op", False))
    if has_attachment:
        operator_clauses.append(("has_attachments", True))
    if favorite_only:
        operator_clauses.append(("is_favorite_op", True))

    extra_filters: dict[str, Any] = {}
    box_arg: str | None = box
    box_not_in: list[str] | None = None
    if favorite is True:
        extra_filters["is_favorite"] = True
        # ``box=ALL_MAIL`` is the "everywhere except trash and spam"
        # anchor used by the dedicated FavoritesPage (see the comment
        # in ``frontend/src/features/emails/pages/FavoritesPage.tsx``).
        # For any other explicit box (SENT / SPAM / TRASH) we respect
        # the caller's choice — otherwise the SENT favourites view
        # would silently surface ALL_MAIL favourites too.
        if box == "ALL_MAIL":
            box_arg = None
            box_not_in = ["TRASH", "SPAM"]

    # ``in:`` overrides the box shown — it wins over the route's ``box``
    # and over the Favourites ``ALL_MAIL`` anchor (``is_favorite`` stays
    # in ``extra_filters``, so ``in:sent`` means "favourites in Sent").
    # Setting ``box_arg`` and clearing ``box_not_in`` keeps the
    # mutually-exclusive contract the repository relies on.
    if parsed.box_override is not None:
        box_arg = parsed.box_override
        box_not_in = None

    try:
        rows = email_metadata_store.list_filtered(
            account_ids, box_arg, tokens, limit, offset,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            group_by_thread=group_by_thread,
            operator_clauses=operator_clauses or None,
            sort=sort, sort_dir=sort_dir,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected email metadata listing error for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise EmailListError(
            "Failed to list email metadata for filtered listing."
        ) from exc

    try:
        total = email_metadata_store.count_filtered(
            account_ids, box_arg, tokens,
            extra_filters=extra_filters or None,
            box_not_in=box_not_in,
            group_by_thread=group_by_thread,
            operator_clauses=operator_clauses or None,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected email metadata count error for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise EmailListError(
            "Failed to count emails while paginating the mailbox listing."
        ) from exc

    return EmailPageOut(
        items=[row_to_email_metadata_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def count_unread_emails(
    mailbox_id: str,
    user_id: str,
    box: str = "ALL_MAIL",
) -> UnreadCountOut:
    """Count unread messages for a mailbox + box, with per-account breakdown.

    Local-only (no provider call). ``box`` is restricted at the router to
    ALL_MAIL | SPAM. Returns the mailbox-wide ``total`` plus one
    ``AccountUnreadDetail`` per account of the mailbox (0 included), so the
    frontend can feed the sidebar badge (total), the per-account tabs and
    the connected-accounts cards from a single response per (mailbox, box).
    Counts INDIVIDUAL messages (not threads) and reflects only the locally
    synced copy.
    """
    ensure_mailbox_access(mailbox_id, user_id)

    try:
        accounts = account_store.list_by_mailbox(mailbox_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected account listing error during unread count for mailbox '%s' (%s): %s",
            mailbox_id, type(exc).__name__, exc,
        )
        raise UnreadCountError(
            "Failed to load mailbox accounts for unread count."
        ) from exc

    account_ids = [str(a["account_id"]) for a in accounts]
    if not account_ids:
        return UnreadCountOut(mailbox_id=mailbox_id, box=box, total=0, accounts=[])

    try:
        counts = email_metadata_store.count_unread_by_account(account_ids, box)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected unread count error for mailbox '%s' box '%s' (%s): %s",
            mailbox_id, box, type(exc).__name__, exc,
        )
        raise UnreadCountError(
            "Failed to count unread emails while building the mailbox unread badge."
        ) from exc

    # Fill 0 for accounts with no unread rows (GROUP BY omits them). Order
    # follows ``list_by_mailbox`` (the same stable order the listing uses).
    details = [
        AccountUnreadDetail(account_id=aid, unread=counts.get(aid, 0))
        for aid in account_ids
    ]
    total = sum(d.unread for d in details)

    return UnreadCountOut(
        mailbox_id=mailbox_id, box=box, total=total, accounts=details,
    )
