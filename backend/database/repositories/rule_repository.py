"""
PostgreSQL rule repository (internal rule engine, carpetas-y-reglas).

CRUD over the ``rules`` table. Mirrors ``virtual_mailbox_repository`` (RealDictCursor,
``_row_to_dict`` normalising UUIDs/timestamps, ``InvalidTextRepresentation`` →
None/[]/False for reads, ``except DatabaseError: raise`` before the fallbacks).
The at-least-one-condition invariant is a table CHECK; a violating write surfaces
as ``QueryError`` (the service also validates it up front for a clean 422).
"""

from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import RuleStore
from database.queries import rules as queries
from database.errors import DatabaseError, QueryError


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("rule_id", "owner_user_id", "target_folder_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class PgRuleStore(RuleStore):
    """PostgreSQL-backed persistence for internal rules."""

    def create(self, rule: dict[str, Any]) -> dict[str, Any]:
        params = {
            "rule_id": rule["rule_id"],
            "owner_user_id": rule["owner_user_id"],
            "name": rule.get("name"),
            "is_enabled": rule.get("is_enabled", True),
            "match_from_email": rule.get("match_from_email"),
            "match_subject_contains": rule.get("match_subject_contains"),
            "target_folder_id": rule["target_folder_id"],
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.INSERT_RULE, params)
                    row = cur.fetchone()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to create rule.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule create error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            raise QueryError("Rule INSERT returned no row.")
        return _row_to_dict(row)

    def get(self, rule_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.GET_RULE, {"rule_id": rule_id})
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get rule.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule get error ({type(exc).__name__}): {exc}"
            ) from exc
        return _row_to_dict(row) if row is not None else None

    def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        return self._list(queries.LIST_RULES_BY_OWNER, owner_user_id)

    def list_active_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        return self._list(queries.LIST_ACTIVE_RULES_BY_OWNER, owner_user_id)

    def _list(self, sql: str, owner_user_id: str) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, {"owner_user_id": owner_user_id})
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list rules by owner.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule list error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def update(self, rule: dict[str, Any]) -> dict[str, Any] | None:
        params = {
            "rule_id": rule["rule_id"],
            "name": rule.get("name"),
            "is_enabled": rule.get("is_enabled", True),
            "match_from_email": rule.get("match_from_email"),
            "match_subject_contains": rule.get("match_subject_contains"),
            "target_folder_id": rule["target_folder_id"],
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.UPDATE_RULE, params)
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update rule.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule update error ({type(exc).__name__}): {exc}"
            ) from exc
        return _row_to_dict(row) if row is not None else None

    def delete(self, rule_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.DELETE_RULE, {"rule_id": rule_id})
                    return cur.rowcount > 0
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to delete rule.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected rule delete error ({type(exc).__name__}): {exc}"
            ) from exc


rule_store = PgRuleStore()
