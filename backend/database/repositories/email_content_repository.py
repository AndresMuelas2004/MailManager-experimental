"""
PostgreSQL email content repository.
"""
from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import EmailContentStore
from database.queries import email_content as queries
from database.errors import DatabaseError, QueryError


class PgEmailContentStore(EmailContentStore):
    """
    PostgreSQL-backed email content persistence.
    """

    def get(self, account_id: str, provider_message_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_BY_MESSAGE_ID,
                        {"account_id": account_id, "provider_message_id": provider_message_id},
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get email content.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email content get error ({type(exc).__name__}): {exc}"
            ) from exc
        return dict(row) if row else None

    def upsert(self, account_id: str, provider_message_id: str, html_body: str | None, text_body: str | None) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPSERT_EMAIL_CONTENT,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                            "html_body": html_body,
                            "text_body": text_body,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to upsert email content.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email content upsert error ({type(exc).__name__}): {exc}"
            ) from exc

    def touch_last_accessed(self, account_id: str, provider_message_id: str) -> None:
        # No ``InvalidTextRepresentation`` guard (mirrors ``upsert``): the only
        # caller is the cache-HIT branch where the row was just read, so the
        # ``account_id`` is always a real UUID. A no-match (race delete) is a
        # silent no-op — the service wrapper is best-effort anyway.
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.TOUCH_LAST_ACCESSED,
                        {
                            "account_id": account_id,
                            "provider_message_id": provider_message_id,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to touch email content last_accessed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email content touch error ({type(exc).__name__}): {exc}"
            ) from exc

    def purge_expired_for_accounts(self, account_ids: list[str]) -> int:
        if not account_ids:
            return 0
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.PURGE_EXPIRED_FOR_ACCOUNTS,
                        {"account_ids": account_ids},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to purge expired email content.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected email content purge error ({type(exc).__name__}): {exc}"
            ) from exc


email_content_store = PgEmailContentStore()
