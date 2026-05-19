"""
PostgreSQL email_attachments / email_attachment_blobs repository (D-06, D-09).
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import EmailAttachmentStore
from database.errors import DatabaseError, QueryError
from database.queries import email_attachments as queries

logger = logging.getLogger(__name__)


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a psycopg2 RealDict row for service-layer consumption."""
    result = dict(row)
    for key in ("attachment_id", "account_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


class PgEmailAttachmentStore(EmailAttachmentStore):
    """PostgreSQL-backed implementation of :py:class:`EmailAttachmentStore`."""

    def upsert_batch(self, rows: list[dict[str, Any]]) -> list[str]:
        """Upsert per-provider partitions and return persisted attachment ids.

        Splits ``rows`` by provider (Gmail rows have ``part_id``;
        Outlook rows have ``provider_attachment_id`` and ``part_id =
        None``) so the ``ON CONFLICT`` clause hits the right partial
        unique index. Mixed batches are supported; each partition runs
        a single ``execute_values`` call.
        """
        if not rows:
            return []

        gmail_rows: list[tuple[Any, ...]] = []
        outlook_rows: list[tuple[Any, ...]] = []
        for r in rows:
            tup = (
                str(r["attachment_id"]),
                str(r["account_id"]),
                str(r["provider_message_id"]),
                r.get("part_id"),
                r.get("provider_attachment_id"),
                str(r["filename"]),
                str(r["mime_type"]),
                int(r["size"]),
                r.get("content_id"),
                bool(r.get("is_inline", False)),
                int(r.get("position", 0)),
            )
            if r.get("part_id") is not None:
                gmail_rows.append(tup)
            else:
                outlook_rows.append(tup)

        persisted: list[str] = []
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    if gmail_rows:
                        gmail_returned = psycopg2.extras.execute_values(
                            cur, queries.UPSERT_EMAIL_ATTACHMENTS_GMAIL,
                            gmail_rows, fetch=True,
                        )
                        persisted.extend(str(row[0]) for row in gmail_returned)
                    if outlook_rows:
                        outlook_returned = psycopg2.extras.execute_values(
                            cur, queries.UPSERT_EMAIL_ATTACHMENTS_OUTLOOK,
                            outlook_rows, fetch=True,
                        )
                        persisted.extend(str(row[0]) for row in outlook_returned)
            return persisted
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to upsert email attachments batch.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email attachments upsert error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_by_message(
        self, account_id: str, provider_message_id: str,
    ) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.LIST_EMAIL_ATTACHMENTS_BY_MESSAGE,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                        },
                    )
                    rows = cur.fetchall()
            return [_row_to_dict(row) for row in rows]
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list email attachments by message.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email attachments list error ({type(exc).__name__}): {exc}"
            ) from exc

    def get_for_download(
        self,
        user_id: str,
        mailbox_id: str,
        account_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_EMAIL_ATTACHMENT_FOR_DOWNLOAD,
                        {
                            "user_id": user_id,
                            "mailbox_id": mailbox_id,
                            "account_id": account_id,
                            "attachment_id": attachment_id,
                        },
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to fetch attachment for download.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected attachment download fetch error ({type(exc).__name__}): {exc}"
            ) from exc
        return _row_to_dict(row) if row else None

    def get_blob(self, attachment_id: str) -> bytes | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.GET_EMAIL_ATTACHMENT_BLOB,
                        {"attachment_id": attachment_id},
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to fetch attachment blob.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected attachment blob fetch error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            return None
        blob = row[0]
        if blob is None:
            return None
        return bytes(blob)

    def insert_blob(self, attachment_id: str, blob: bytes) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.INSERT_EMAIL_ATTACHMENT_BLOB,
                        {
                            "attachment_id": attachment_id,
                            "blob": psycopg2.Binary(blob),
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to insert attachment blob.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected attachment blob insert error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_unavailable(self, attachment_id: str) -> None:
        self._single_update(
            queries.MARK_EMAIL_ATTACHMENT_UNAVAILABLE,
            {"attachment_id": attachment_id},
            error_label="mark unavailable",
        )

    def touch_last_accessed(self, attachment_id: str) -> None:
        self._single_update(
            queries.TOUCH_EMAIL_ATTACHMENT_LAST_ACCESSED,
            {"attachment_id": attachment_id},
            error_label="touch last_accessed_at",
        )

    def _single_update(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        error_label: str,
    ) -> None:
        """Shared scaffold for single-row UPDATE statements with the standard
        error pattern. Avoids duplicating the try/except across the
        ``mark_unavailable`` / ``touch_last_accessed`` siblings.
        """
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        except psycopg2.errors.InvalidTextRepresentation:
            logger.debug(
                "_single_update (%s) skipped — invalid UUID in params: %s",
                error_label, params,
            )
            return
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError(f"Failed to {error_label}.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected {error_label} error ({type(exc).__name__}): {exc}"
            ) from exc

    def purge_expired_blobs(self) -> tuple[int, int]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.PURGE_EXPIRED_BLOBS)
                    rows = cur.fetchall()
            count = len(rows)
            freed = sum(int(r[1] or 0) for r in rows)
            return count, freed
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to purge expired attachment blobs.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected attachment purge error ({type(exc).__name__}): {exc}"
            ) from exc


email_attachment_store = PgEmailAttachmentStore()
