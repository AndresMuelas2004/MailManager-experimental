"""
PostgreSQL rule-apply job repository ("apply to existing", carpetas-y-reglas).

One row per RULE in ``rule_apply_jobs`` — a resumable checkpoint driven by the
SAME in-process worker as the backfill / draft-sync queues. Clone of
``account_backfill_repository`` keyed by ``rule_id`` (the apply is per-rule /
per-user) with a ``page_cursor`` + ``processed_count`` checkpoint.
"""

from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import RuleApplyJobStore
from database.queries import rule_apply_jobs as queries
from database.errors import DatabaseError, QueryError


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("rule_id", "owner_user_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at", "completed_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class PgRuleApplyJobStore(RuleApplyJobStore):
    """PostgreSQL-backed persistence for rule-apply job checkpoints."""

    def enqueue(self, rule_id: str, owner_user_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.ENQUEUE,
                        {"rule_id": rule_id, "owner_user_id": owner_user_id},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to enqueue rule apply job.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply enqueue error ({type(exc).__name__}): {exc}"
            ) from exc

    def get(self, rule_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.GET, {"rule_id": rule_id})
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get rule apply job.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply get error ({type(exc).__name__}): {exc}"
            ) from exc
        return _row_to_dict(row) if row is not None else None

    def claim_next_batch(self, limit: int) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.CLAIM_NEXT_BATCH, {"limit": limit})
                    rows = cur.fetchall()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to claim next rule apply batch.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply claim error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def update_progress(
        self, rule_id: str, processed_count: int, page_cursor: str | None,
    ) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_PROGRESS,
                        {
                            "rule_id": rule_id,
                            "processed_count": processed_count,
                            "page_cursor": page_cursor,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update rule apply progress.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply progress update error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_completed(self, rule_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.MARK_COMPLETED, {"rule_id": rule_id})
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark rule apply job completed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply mark-completed error ({type(exc).__name__}): {exc}"
            ) from exc

    def mark_failed(self, rule_id: str, error: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.MARK_FAILED, {"rule_id": rule_id, "error": error},
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to mark rule apply job failed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply mark-failed error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_running_to_pending(self) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.RESET_RUNNING_TO_PENDING)
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset running rule apply jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply reset error ({type(exc).__name__}): {exc}"
            ) from exc

    def reset_retriable_failed_to_pending(
        self, max_attempts: int, backoff_seconds: int,
    ) -> int:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.RESET_RETRIABLE_FAILED_TO_PENDING,
                        {"max_attempts": max_attempts, "backoff_seconds": backoff_seconds},
                    )
                    return cur.rowcount
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reset retriable failed rule apply jobs to pending.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule apply retriable reset error ({type(exc).__name__}): {exc}"
            ) from exc


rule_apply_store = PgRuleApplyJobStore()
