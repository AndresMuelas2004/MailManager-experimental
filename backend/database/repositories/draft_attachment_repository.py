"""
PostgreSQL draft_attachments repository (D-07).
"""
from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import DraftAttachmentStore
from database.errors import DatabaseError, QueryError
from database.queries import draft_attachments as queries


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a psycopg2 RealDict row for service-layer consumption.

    Casts UUID columns to ``str`` and converts the ``BYTEA`` blob (when
    present) to ``bytes`` so callers can pass it straight into the MIME
    builder without an extra cast.
    """
    result = dict(row)
    for key in ("draft_attachment_id", "account_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    if isinstance(result.get("blob"), memoryview):
        result["blob"] = bytes(result["blob"])
    return result


class PgDraftAttachmentStore(DraftAttachmentStore):
    """PostgreSQL-backed implementation of :py:class:`DraftAttachmentStore`."""

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        # ``position`` is computed atomically inside the INSERT statement —
        # see :py:data:`queries.INSERT_DRAFT_ATTACHMENT`. The caller must
        # NOT pre-resolve it (any value passed here is ignored).
        params = {
            "draft_attachment_id": str(row["draft_attachment_id"]),
            "account_id": str(row["account_id"]),
            "provider_draft_id": str(row["provider_draft_id"]),
            "filename": str(row["filename"]),
            "mime_type": str(row["mime_type"]),
            "size": int(row["size"]),
            "content_id": row.get("content_id"),
            "is_inline": bool(row.get("is_inline", False)),
            "blob": psycopg2.Binary(row["blob"]) if row.get("blob") is not None else None,
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.INSERT_DRAFT_ATTACHMENT, params)
                    inserted = cur.fetchone()
            if inserted is None:
                raise QueryError("Draft attachment insert returned no row.")
            return _row_to_dict(inserted)
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to insert draft attachment.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachment insert error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_by_draft(
        self, account_id: str, provider_draft_id: str,
    ) -> list[dict[str, Any]]:
        return self._list_by_draft(
            queries.LIST_DRAFT_ATTACHMENTS_BY_DRAFT, account_id, provider_draft_id,
        )

    def list_by_draft_with_blob(
        self, account_id: str, provider_draft_id: str,
    ) -> list[dict[str, Any]]:
        """Same shape as :py:meth:`list_by_draft` plus the binary, used by the
        send path so it can avoid the 1 + N round trips of a list-then-per-row-get
        pattern. D-03 caps drafts at 25 rows so the payload is bounded."""
        return self._list_by_draft(
            queries.LIST_DRAFT_ATTACHMENTS_BY_DRAFT_WITH_BLOB, account_id, provider_draft_id,
        )

    def _list_by_draft(
        self, sql: str, account_id: str, provider_draft_id: str,
    ) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        sql,
                        {"account_id": account_id, "provider_draft_id": provider_draft_id},
                    )
                    rows = cur.fetchall()
            return [_row_to_dict(r) for r in rows]
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list draft attachments.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachments list error ({type(exc).__name__}): {exc}"
            ) from exc

    def get(self, draft_attachment_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_DRAFT_ATTACHMENT,
                        {"draft_attachment_id": draft_attachment_id},
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to fetch draft attachment.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachment get error ({type(exc).__name__}): {exc}"
            ) from exc
        return _row_to_dict(row) if row else None

    def delete(self, draft_attachment_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.DELETE_DRAFT_ATTACHMENT,
                        {"draft_attachment_id": draft_attachment_id},
                    )
                    return cur.fetchone() is not None
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to delete draft attachment.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachment delete error ({type(exc).__name__}): {exc}"
            ) from exc

    def update_provider_attachment_id(
        self, draft_attachment_id: str, provider_attachment_id: str,
    ) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_DRAFT_ATTACHMENT_PROVIDER_ID,
                        {
                            "draft_attachment_id": draft_attachment_id,
                            "provider_attachment_id": provider_attachment_id,
                        },
                    )
                    if cur.fetchone() is None:
                        # Race with CASCADE delete (the user pulled the draft
                        # mid-send) — surface explicitly so callers can decide
                        # how to react instead of silently losing the D-27
                        # partial-success persistence contract.
                        raise QueryError(
                            "draft_attachment row missing during provider id update.",
                            detail={"draft_attachment_id": draft_attachment_id},
                        )
        except psycopg2.errors.InvalidTextRepresentation as exc:
            # Bad UUID at the boundary breaks the D-27 contract loudly so the
            # caller can repair the upstream value rather than silently losing
            # the persistence step.
            raise QueryError(
                "Invalid draft_attachment_id passed to update_provider_attachment_id.",
                detail={"draft_attachment_id": draft_attachment_id},
            ) from exc
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError(
                "Failed to update draft attachment provider id."
            ) from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachment update error ({type(exc).__name__}): {exc}"
            ) from exc

    def batch_update_provider_attachment_ids(
        self, pairs: list[tuple[str, str]],
    ) -> int:
        if not pairs:
            return 0
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        queries.BATCH_UPDATE_DRAFT_ATTACHMENT_PROVIDER_IDS,
                        pairs,
                        # RETURNING means we must consume rows to get the count.
                    )
                    rows = cur.fetchall()
                    return len(rows)
        except psycopg2.errors.InvalidTextRepresentation as exc:
            # Bad UUID at the boundary breaks the D-27 contract loudly so the
            # caller can repair the upstream value rather than losing the
            # persistence step.
            raise QueryError(
                "Invalid draft_attachment_id passed to batch_update_provider_attachment_ids.",
                detail={"pairs": [pid for pid, _ in pairs]},
            ) from exc
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError(
                "Failed to batch update draft attachment provider ids."
            ) from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft attachment batch update error ({type(exc).__name__}): {exc}"
            ) from exc


draft_attachment_store = PgDraftAttachmentStore()
