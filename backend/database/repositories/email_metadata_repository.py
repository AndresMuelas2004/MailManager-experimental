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

    def list_provider_message_ids(self, account_id: str) -> list[str]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.LIST_PROVIDER_MESSAGE_IDS_BY_ACCOUNT, {"account_id": account_id})
                    return [row[0] for row in cur.fetchall()]
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list provider message IDs.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected list provider message IDs error ({type(exc).__name__}): {exc}"
            ) from exc

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
    ) -> list[dict[str, Any]]:
        # Callers MUST pass at most one of ``box`` / ``box_in`` /
        # ``box_not_in`` — exclusivity is enforced upstream (services).
        # Passing more than one here would emit duplicated ``AND box ...``
        # clauses that AND together and silently return zero rows.
        if not account_ids:
            return []
        try:
            params: dict[str, Any] = {
                "account_ids": account_ids,
                "limit": limit,
                "offset": offset,
            }

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
            extra_predicate = (
                "AND " + " AND ".join(extra_clauses) if extra_clauses else ""
            )

            sql = queries.LIST_FILTERED.format(
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


email_metadata_store = PgEmailMetadataStore()
