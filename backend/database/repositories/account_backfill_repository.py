"""
PostgreSQL account-backfill job repository (background initial mass backfill).
"""

from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import AccountBackfillStore
from database.queries import account_backfill
from database.errors import DatabaseError, QueryError


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("account_id", "mailbox_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at", "completed_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class PgAccountBackfillStore(AccountBackfillStore):
    """PostgreSQL-backed persistence for backfill job checkpoints."""

    def enqueue(
        self,
        account_id: str,
        mailbox_id: str,
        provider: str,
        target_total: int,
    ) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.ENQUEUE,
                        {
                            "account_id": account_id,
                            "mailbox_id": mailbox_id,
                            "provider": provider,
                            "target_total": target_total,
                        },
                    )
                    # A no-op (row exists and is not 'failed') affects zero
                    # rows — a valid outcome, not an error. Do NOT check rowcount.
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to enqueue backfill job.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill enqueue error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_by_mailbox(self, mailbox_id: str) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        account_backfill.LIST_BY_MAILBOX,
                        {"mailbox_id": mailbox_id},
                    )
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list backfill jobs by mailbox.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill list error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def claim_next_batch(self, limit: int) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(account_backfill.CLAIM_NEXT_BATCH, {"limit": limit})
                    rows = cur.fetchall()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to claim next backfill batch.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill claim error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def update_progress(
        self, account_id: str, fetched_count: int, page_cursor: str | None,
    ) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.UPDATE_PROGRESS,
                        {
                            "account_id": account_id,
                            "fetched_count": fetched_count,
                            "page_cursor": page_cursor,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update backfill progress.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill progress update error ({type(exc).__name__}): {exc}"
            ) from exc

    def set_anchor(self, account_id: str, initial_sync_cursor: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.SET_ANCHOR,
                        {
                            "account_id": account_id,
                            "initial_sync_cursor": initial_sync_cursor,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to set backfill anchor.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill anchor set error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_completed(self, account_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.MARK_COMPLETED, {"account_id": account_id},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark backfill job completed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill mark-completed error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_failed(self, account_id: str, error: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.MARK_FAILED,
                        {"account_id": account_id, "error": error},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark backfill job failed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill mark-failed error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_running_to_pending(self) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(account_backfill.RESET_RUNNING_TO_PENDING)
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset running backfill jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill reset error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_retriable_failed_to_pending(
        self, max_attempts: int, backoff_seconds: int,
    ) -> int:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        account_backfill.RESET_RETRIABLE_FAILED_TO_PENDING,
                        {"max_attempts": max_attempts, "backoff_seconds": backoff_seconds},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset retriable failed backfill jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected backfill retriable reset error ({type(exc).__name__}): {exc}"
            ) from exc


account_backfill_store = PgAccountBackfillStore()
