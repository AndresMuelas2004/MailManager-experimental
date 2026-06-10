"""
PostgreSQL email metadata repository.
"""

from __future__ import annotations

from typing import Any, Callable

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import EmailMetadataStore
from database.queries import email_metadata as queries
from database.errors import DatabaseError, QueryError


def _escape_like(token: str) -> str:
    """Escape backslash, %, and _ for safe use inside an ILIKE pattern."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Closed whitelist for ``list_filtered``'s ``extra_filters`` keys. Each
# builder returns ``(sql_clause, params_dict)`` so the repository can
# concatenate clauses with " AND " and merge the named parameters into
# the cursor call. Unknown keys are silently dropped — the API schema
# (``VirtualMailboxFilterPayload``) is the layer that surfaces typos as
# 422; here we keep the SQL surface tight against injection. To add a
# new key, register a builder AND update ``ALLOWED_FILTER_KEYS`` in the
# schema in the same change.
def _build_is_read_clause(value: Any) -> tuple[str, dict[str, Any]]:
    return "is_read = %(extra_is_read)s", {"extra_is_read": bool(value)}


def _build_is_favorite_clause(value: Any) -> tuple[str, dict[str, Any]]:
    return "is_favorite = %(extra_is_favorite)s", {"extra_is_favorite": bool(value)}


def _build_from_email_clause(value: Any) -> tuple[str, dict[str, Any]]:
    return (
        "lower(coalesce(from_email, '')) = lower(%(extra_from_email)s)",
        {"extra_from_email": str(value)},
    )


def _build_subject_contains_clause(value: Any) -> tuple[str, dict[str, Any]]:
    escaped = _escape_like(str(value))
    return (
        "unaccent(lower(coalesce(subject, ''))) "
        "ILIKE unaccent(lower(%(extra_subject_contains)s))",
        {"extra_subject_contains": f"%{escaped}%"},
    )


_EXTRA_FILTER_BUILDERS: dict[str, Callable[[Any], tuple[str, dict[str, Any]]]] = {
    "is_read": _build_is_read_clause,
    "is_favorite": _build_is_favorite_clause,
    "from_email": _build_from_email_clause,
    "subject_contains": _build_subject_contains_clause,
}


def _select_list_template(group_by_thread: bool, distinct: bool) -> str:
    """Pick the LIST template for the (group_by_thread, distinct) combination.

    The COUNT selector below MUST track this 2x2 matrix exactly so the
    paginated total counts what the page lists.
    """
    if group_by_thread:
        return (
            queries.LIST_GROUPED_BY_THREAD_DISTINCT
            if distinct
            else queries.LIST_GROUPED_BY_THREAD
        )
    return queries.LIST_FILTERED_DISTINCT if distinct else queries.LIST_FILTERED


def _select_count_template(group_by_thread: bool, distinct: bool) -> str:
    """Pick the COUNT template matching :py:func:`_select_list_template`."""
    if group_by_thread:
        return (
            queries.COUNT_GROUPED_BY_THREAD_DISTINCT
            if distinct
            else queries.COUNT_GROUPED_BY_THREAD
        )
    return queries.COUNT_FILTERED_DISTINCT if distinct else queries.COUNT_FILTERED


# Operator-clause builders for the lupa's Gmail-style operators (from:/to:/
# subject:/has:attachment/before:/after:/is:read|unread|favorite/in: — ``in:``
# is resolved as a box override in the service, not here). This registry is
# INTENTIONALLY separate from ``_EXTRA_FILTER_BUILDERS``:
#   - These operators are ad-hoc search, NOT saveable virtual-mailbox filters,
#     so they have NO counterpart in ``VirtualMailboxFilterPayload`` /
#     ``ALLOWED_FILTER_KEYS`` (the "two whitelists in lockstep" rule does not
#     apply to them — see repository_guide.md / the feature plan §7).
#   - The ``kind`` keys carry an ``_op`` suffix (or a distinct name) and the
#     parameter names are ``op{idx}`` per occurrence, so an operator clause and
#     a saved filter touching the SAME column (e.g. ``subject_contains`` saved
#     + ``subject:`` from the lupa, or ``is_read`` saved + ``is:read``) emit two
#     independent ANDed clauses with disjoint params instead of colliding. A
#     contradiction (``is:read``+``is:unread``, inverted date range) thus yields
#     zero rows naturally via two incompatible AND clauses.
# Each builder takes ``(value, idx)`` and returns ``(clause_sql, params)`` with
# a unique ``op{idx}`` parameter name so repeated operators do not collide.
def _op_from_contains(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (
        f"(unaccent(lower(coalesce(from_email, ''))) ILIKE unaccent(lower(%({p})s)) "
        f"OR unaccent(lower(coalesce(from_name, ''))) ILIKE unaccent(lower(%({p})s)))",
        {p: f"%{_escape_like(str(value))}%"},
    )


def _op_to_contains(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (
        f"(unaccent(lower(coalesce(to_email, ''))) ILIKE unaccent(lower(%({p})s)) "
        f"OR unaccent(lower(coalesce(to_name, ''))) ILIKE unaccent(lower(%({p})s)))",
        {p: f"%{_escape_like(str(value))}%"},
    )


def _op_subject_contains(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (
        f"unaccent(lower(coalesce(subject, ''))) ILIKE unaccent(lower(%({p})s))",
        {p: f"%{_escape_like(str(value))}%"},
    )


def _op_has_attachments(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (f"has_attachments = %({p})s", {p: bool(value)})


def _op_received_after(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    # ``value`` is a tz-aware datetime; psycopg2 adapts it for the
    # comparison against ``received_at`` (TIMESTAMPTZ).
    return (f"received_at >= %({p})s", {p: value})


def _op_received_before(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (f"received_at < %({p})s", {p: value})


def _op_is_read(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (f"is_read = %({p})s", {p: bool(value)})


def _op_is_favorite(value: Any, idx: int) -> tuple[str, dict[str, Any]]:
    p = f"op{idx}"
    return (f"is_favorite = %({p})s", {p: bool(value)})


_OPERATOR_CLAUSE_BUILDERS: dict[str, Callable[[Any, int], tuple[str, dict[str, Any]]]] = {
    "from_contains": _op_from_contains,
    "to_contains": _op_to_contains,
    "subject_contains_op": _op_subject_contains,
    "has_attachments": _op_has_attachments,
    "received_after": _op_received_after,
    "received_before": _op_received_before,
    "is_read_op": _op_is_read,
    "is_favorite_op": _op_is_favorite,
}


def _build_recipient_token_predicate(
    tokens: list[str],
    email_col: str,
    name_col: str,
    params: dict[str, Any],
) -> str:
    """Build the AND-chained token predicate for one ``(email, name)``
    column pair, mutating *params* with the shared ``rtok{i}`` keys.

    Each token is OR'd across the two columns with accent-/case-
    insensitive substring match — same shape as the search predicate in
    ``_build_filter_predicates``. Returns ``""`` when there are no
    tokens (so the SQL slot stays empty). ``email_col`` / ``name_col``
    are hardcoded by the caller (never user input), so concatenating
    them into the clause carries no injection risk.

    Both UNION ALL branches of ``LIST_RECIPIENT_SUGGESTIONS`` reference
    the SAME ``%(rtok{i})s`` params, so this helper is called once per
    branch and writes the same values into *params* on each call
    (idempotent — the second call overwrites with identical values).
    """
    if not tokens:
        return ""
    clauses: list[str] = []
    for i, token in enumerate(tokens):
        key = f"rtok{i}"
        params[key] = f"%{_escape_like(token)}%"
        clauses.append(
            f"(unaccent(lower(coalesce({email_col}, ''))) ILIKE unaccent(lower(%({key})s))"
            f" OR unaccent(lower(coalesce({name_col}, ''))) ILIKE unaccent(lower(%({key})s)))"
        )
    return "AND " + " AND ".join(clauses)


class PgEmailMetadataStore(EmailMetadataStore):
    """
    PostgreSQL-backed email metadata persistence.
    """

    def _execute_batch_values(self, query: str, rows: list[tuple], error_msg: str) -> int:
        """Shared helper for execute_values batch operations."""
        if not rows:
            return 0
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, query, rows, page_size=500)
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError(error_msg) from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected {error_msg.lower()} ({type(exc).__name__}): {exc}"
            ) from exc

    def upsert_batch(self, account_id: str, rows: list[tuple]) -> int:
        # account_id is embedded in each tuple by the caller; this parameter
        # exists for contract symmetry only.
        return self._execute_batch_values(
            queries.UPSERT_EMAIL_METADATA_BATCH,
            rows,
            "Failed to upsert email metadata batch.",
        )

    def delete_batch_by_message_ids(self, account_id: str, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.DELETE_BATCH_BY_MESSAGE_IDS,
                        {"account_id": account_id, "message_ids": message_ids},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to delete email metadata batch by message IDs.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email metadata delete batch error ({type(exc).__name__}): {exc}"
            ) from exc

    def update_labels_batch(self, account_id: str, rows: list[tuple]) -> int:
        # account_id is embedded in each tuple by the caller; this parameter
        # exists for contract symmetry only.
        return self._execute_batch_values(
            queries.UPDATE_LABELS_BATCH,
            rows,
            "Failed to update email metadata labels batch.",
        )

    def update_read_status_batch(self, account_id: str, rows: list[tuple]) -> int:
        # account_id is embedded in each tuple by the caller; this parameter
        # exists for contract symmetry only.
        return self._execute_batch_values(
            queries.UPDATE_READ_STATUS_BATCH,
            rows,
            "Failed to update email read status batch.",
        )

    def list_provider_message_ids_not_in(
        self, account_id: str, exclude_ids: list[str],
    ) -> list[str]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.LIST_PROVIDER_MESSAGE_IDS_NOT_IN,
                        {"account_id": account_id, "exclude_ids": list(exclude_ids)},
                    )
                    return [row[0] for row in cur.fetchall()]
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list provider message IDs not in exclude set.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected list provider message IDs not-in error ({type(exc).__name__}): {exc}"
            ) from exc

    def exists(self, account_id: str, provider_message_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.EXISTS_BY_MESSAGE_ID,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                        },
                    )
                    row = cur.fetchone()
                    return row is not None
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to check email metadata existence.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email metadata exists check error ({type(exc).__name__}): {exc}"
            ) from exc

    def get_metadata(
        self, account_id: str, provider_message_id: str,
    ) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_METADATA_BY_MESSAGE,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                        },
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get email metadata row by message id.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected get email metadata row error ({type(exc).__name__}): {exc}"
            ) from exc
        return dict(row) if row is not None else None


    def get_trash_emails_by_ids(self, account_id: str, message_ids: list[str]) -> list[dict[str, Any]]:
        if not message_ids:
            return []
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_TRASH_EMAILS_BY_IDS,
                        {"account_id": account_id, "message_ids": message_ids},
                    )
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get trash emails by IDs.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected get trash emails error ({type(exc).__name__}): {exc}"
            ) from exc
        return [dict(row) for row in rows]

    def mark_as_deleted_batch(self, account_id: str, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.MARK_AS_DELETED_BATCH,
                        {"account_id": account_id, "message_ids": message_ids},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark emails as deleted.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected mark as deleted error ({type(exc).__name__}): {exc}"
            ) from exc

    def restore_from_trash_batch(self, account_id: str, rows: list[tuple]) -> int:
        return self._execute_batch_values(
            queries.RESTORE_FROM_TRASH_BATCH, rows, "Failed to restore emails from trash.",
        )

    def restore_from_trash_discovered_batch(self, account_id: str, rows: list[tuple]) -> int:
        return self._execute_batch_values(
            queries.RESTORE_FROM_TRASH_DISCOVERED_BATCH, rows,
            "Failed to restore emails with discovered box.",
        )

    def move_to_trash_batch(self, account_id: str, rows: list[tuple]) -> int:
        return self._execute_batch_values(
            queries.MOVE_TO_TRASH_BATCH, rows, "Failed to move emails to trash.",
        )

    def update_spam_status_batch(self, account_id: str, rows: list[tuple]) -> int:
        # account_id is embedded in each tuple by the caller; this parameter
        # exists for contract symmetry only.
        return self._execute_batch_values(
            queries.MOVE_SPAM_BATCH, rows, "Failed to update email spam status batch.",
        )


    def _build_filter_predicates(
        self,
        account_ids: list[str],
        box: str | None,
        tokens: list[str],
        *,
        extra_filters: dict[str, Any] | None,
        box_in: list[str] | None,
        box_not_in: list[str] | None,
        operator_clauses: list[tuple[str, Any]] | None = None,
    ) -> tuple[str, str, str, dict[str, Any]]:
        """Build the ``{box_predicate}``/``{search_predicate}``/``{extra_predicate}``
        slots and the named params shared by every filtered query.

        Single source of truth so ``list_filtered`` (SELECT) and
        ``count_filtered`` (COUNT) stay in lockstep — a new filter
        criterion lands here once and both the listing and its total
        pick it up. ``account_ids`` always goes into the returned params
        (the WHERE clause references it in every query). ``limit`` /
        ``offset`` are NOT added here — only the listing queries carry
        them.

        Callers MUST pass at most one of ``box`` / ``box_in`` /
        ``box_not_in`` — exclusivity is enforced upstream (services).
        Passing more than one here would emit duplicated ``AND box ...``
        clauses that AND together and silently return zero rows.

        ``operator_clauses`` (the lupa's Gmail-style operators) are
        appended to the SAME ``{extra_predicate}`` slot AFTER the
        ``extra_filters`` dict clauses — no new SQL slot. Their parameter
        names (``op{idx}``) are disjoint from ``extra_*`` (saved filters)
        and ``tok{i}`` (free text), so the three never collide and an
        operator that touches the same column as a saved filter just
        AND-s a second clause. When ``operator_clauses`` is empty the
        emitted SQL is byte-for-byte identical to the pre-operator query.
        """
        params: dict[str, Any] = {"account_ids": account_ids}

        if box is not None:
            params["box"] = box
            box_predicate = "AND box = %(box)s"
        elif box_in:
            params["box_in_list"] = list(box_in)
            box_predicate = "AND box = ANY(%(box_in_list)s)"
        elif box_not_in:
            # Empty list means "do not exclude anything" — callers use
            # it to opt back into seeing TRASH/SPAM. Leave the slot
            # empty so we do not emit a tautological ``NOT (box = ANY('{}'))``.
            params["box_not_in_list"] = list(box_not_in)
            box_predicate = "AND NOT (box = ANY(%(box_not_in_list)s))"
        else:
            box_predicate = ""

        if tokens:
            clauses: list[str] = []
            for i, token in enumerate(tokens):
                key = f"tok{i}"
                params[key] = f"%{_escape_like(token)}%"
                clauses.append(
                    f"(unaccent(lower(coalesce(subject, ''))) ILIKE unaccent(lower(%({key})s))"
                    f" OR unaccent(lower(coalesce(from_email, ''))) ILIKE unaccent(lower(%({key})s))"
                    f" OR unaccent(lower(coalesce(from_name, ''))) ILIKE unaccent(lower(%({key})s)))"
                )
            search_predicate = "AND " + " AND ".join(clauses)
        else:
            search_predicate = ""

        extra_clauses: list[str] = []
        if extra_filters:
            for key, value in extra_filters.items():
                builder = _EXTRA_FILTER_BUILDERS.get(key)
                if builder is None:
                    continue
                clause, extra_params = builder(value)
                extra_clauses.append(clause)
                params.update(extra_params)
        if operator_clauses:
            for i, (kind, value) in enumerate(operator_clauses):
                op_builder = _OPERATOR_CLAUSE_BUILDERS.get(kind)
                if op_builder is None:
                    continue
                clause, op_params = op_builder(value, i)
                extra_clauses.append(clause)
                params.update(op_params)
        extra_predicate = (
            "AND " + " AND ".join(extra_clauses) if extra_clauses else ""
        )

        return box_predicate, search_predicate, extra_predicate, params

    def list_filtered(
        self,
        account_ids: list[str],
        box: str | None,
        tokens: list[str],
        limit: int,
        offset: int,
        *,
        extra_filters: dict[str, Any] | None = None,
        box_in: list[str] | None = None,
        box_not_in: list[str] | None = None,
        distinct_provider_message_id: bool = False,
        group_by_thread: bool = False,
        operator_clauses: list[tuple[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not account_ids:
            return []
        try:
            box_predicate, search_predicate, extra_predicate, params = (
                self._build_filter_predicates(
                    account_ids, box, tokens,
                    extra_filters=extra_filters,
                    box_in=box_in,
                    box_not_in=box_not_in,
                    operator_clauses=operator_clauses,
                )
            )
            params["limit"] = limit
            params["offset"] = offset

            template = _select_list_template(
                group_by_thread, distinct_provider_message_id,
            )
            sql = template.format(
                box_predicate=box_predicate,
                search_predicate=search_predicate,
                extra_predicate=extra_predicate,
            )
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list filtered email metadata.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected list filtered email metadata error ({type(exc).__name__}): {exc}"
            ) from exc
        return [dict(row) for row in rows]

    def count_filtered(
        self,
        account_ids: list[str],
        box: str | None,
        tokens: list[str],
        *,
        extra_filters: dict[str, Any] | None = None,
        box_in: list[str] | None = None,
        box_not_in: list[str] | None = None,
        distinct_provider_message_id: bool = False,
        group_by_thread: bool = False,
        operator_clauses: list[tuple[str, Any]] | None = None,
    ) -> int:
        # Mirror ``list_filtered``'s empty-accounts short-circuit: never
        # touch the DB when there is nothing to count.
        if not account_ids:
            return 0
        try:
            box_predicate, search_predicate, extra_predicate, params = (
                self._build_filter_predicates(
                    account_ids, box, tokens,
                    extra_filters=extra_filters,
                    box_in=box_in,
                    box_not_in=box_not_in,
                    operator_clauses=operator_clauses,
                )
            )
            template = _select_count_template(
                group_by_thread, distinct_provider_message_id,
            )
            sql = template.format(
                box_predicate=box_predicate,
                search_predicate=search_predicate,
                extra_predicate=extra_predicate,
            )
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return 0
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to count filtered email metadata.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected count filtered email metadata error ({type(exc).__name__}): {exc}"
            ) from exc
        return int(row[0]) if row else 0

    def update_has_attachments(self, account_id: str, provider_message_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_HAS_ATTACHMENTS,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                        },
                    )
        except psycopg2.errors.InvalidTextRepresentation as exc:
            # Bad UUID at the boundary breaks the B.lazy invariant loudly so
            # the caller can repair the upstream value rather than silently
            # leaving ``has_attachments`` stale.
            raise QueryError(
                "Invalid identifier passed to update_has_attachments.",
                detail={"account_id": account_id, "provider_message_id": provider_message_id},
            ) from exc
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to recompute has_attachments.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected has_attachments recompute error ({type(exc).__name__}): {exc}"
            ) from exc

    def update_favorite(
        self,
        account_id: str,
        provider_message_id: str,
        is_favorite: bool,
    ) -> bool:
        # ``RETURNING provider_message_id`` lets us detect "row not found"
        # without a second roundtrip: ``fetchone()`` returns ``None`` when
        # the WHERE clause matched zero rows. The service translates the
        # ``False`` return into ``EmailNotFound`` (404) — consistent with
        # the ``exists`` pre-check semantics.
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_FAVORITE_STATUS,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                            "is_favorite": is_favorite,
                        },
                    )
                    return cur.fetchone() is not None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update email favorite status.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email favorite update error ({type(exc).__name__}): {exc}"
            ) from exc

    def sync_favorites_for_account(
        self,
        account_id: str,
        favorite_ids: list[str],
    ) -> int:
        # Single-statement full replacement: every row of the account is
        # touched in one UPDATE (``is_favorite = provider_message_id =
        # ANY(true_ids)``). An empty ``favorite_ids`` is valid and means
        # "no message is favourite anymore for this account" — PostgreSQL
        # evaluates ``= ANY('{}')`` as FALSE for every row, so the single
        # statement covers the "clear all" case too. ``cur.rowcount``
        # returns the total rows touched for the account (useful for
        # observability — see ``FavoriteSyncResponse.total_synced``).
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.SYNC_FAVORITES_FOR_ACCOUNT,
                        {
                            "account_id": account_id,
                            "true_ids": favorite_ids,
                        },
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to sync email favorites for account.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email favorites sync error ({type(exc).__name__}): {exc}"
            ) from exc

    def set_favorites_true_batch(
        self,
        account_id: str,
        provider_message_ids: list[str],
    ) -> int:
        # Single statement marking the supplied subset TRUE (the thread's
        # favourite members during a conversation lazy-sync). Never forces
        # FALSE — the full-replacement path is ``sync_favorites_for_account``.
        # An empty list emits ``= ANY('{}')`` (matches nothing); the service
        # guards against it, but the query is harmless regardless.
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_FAVORITES_TRUE_BATCH,
                        {
                            "account_id": account_id,
                            "true_ids": provider_message_ids,
                        },
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to batch-mark email favorites.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email favorites batch update error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_recipient_suggestions(
        self,
        account_ids: list[str],
        tokens: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        # Mirror the empty-accounts short-circuit of the filtered queries:
        # never touch the DB when there is nothing to aggregate.
        if not account_ids:
            return []
        try:
            params: dict[str, Any] = {"account_ids": account_ids, "limit": limit}
            from_pred = _build_recipient_token_predicate(
                tokens, "from_email", "from_name", params
            )
            to_pred = _build_recipient_token_predicate(
                tokens, "to_email", "to_name", params
            )
            sql = queries.LIST_RECIPIENT_SUGGESTIONS.format(
                from_token_predicate=from_pred,
                to_token_predicate=to_pred,
            )
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list recipient suggestions.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected recipient suggestions error ({type(exc).__name__}): {exc}"
            ) from exc
        return [dict(row) for row in rows]


email_metadata_store = PgEmailMetadataStore()
