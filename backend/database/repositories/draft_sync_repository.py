"""
PostgreSQL draft-sync job repository (reliable server-side draft sync).

Simplified clone of ``account_backfill_repository`` — the draft sync is a single
non-paginated operation per account, so there is no progress/anchor persistence.
"""

from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from database import connection
from database.contracts import DraftSyncStore
from database.queries import draft_sync
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


class PgDraftSyncStore(DraftSyncStore):
    """PostgreSQL-backed persistence for draft-sync jobs."""

    def enqueue(self, account_id: str, mailbox_id: str, provider: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        draft_sync.ENQUEUE,
                        {
                            "account_id": account_id,
                            "mailbox_id": mailbox_id,
                            "provider": provider,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to enqueue draft sync job.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync enqueue error ({type(exc).__name__}): {exc}"
            ) from exc

    def claim_next_batch(self, limit: int) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(draft_sync.CLAIM_NEXT_BATCH, {"limit": limit})
                    rows = cur.fetchall()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to claim next draft sync batch.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync claim error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def mark_completed(self, account_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        draft_sync.MARK_COMPLETED, {"account_id": account_id},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark draft sync job completed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync mark-completed error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_failed(self, account_id: str, error: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        draft_sync.MARK_FAILED,
                        {"account_id": account_id, "error": error},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark draft sync job failed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync mark-failed error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_running_to_pending(self) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(draft_sync.RESET_RUNNING_TO_PENDING)
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset running draft sync jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync reset error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_retriable_failed_to_pending(
        self, max_attempts: int, backoff_seconds: int,
    ) -> int:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        draft_sync.RESET_RETRIABLE_FAILED_TO_PENDING,
                        {"max_attempts": max_attempts, "backoff_seconds": backoff_seconds},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset retriable failed draft sync jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected draft sync retriable reset error ({type(exc).__name__}): {exc}"
            ) from exc


draft_sync_store = PgDraftSyncStore()
