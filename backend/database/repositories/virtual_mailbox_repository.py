"""
PostgreSQL virtual-mailbox repository.

Holds only the CRUD over the ``virtual_mailboxes`` table. The actual
filter translation (account_ids + filter_payload → predicates against
``email_metadata``) lives in the service layer because it must reuse
``EmailMetadataStore.list_filtered`` and resolve mailbox/account
ownership via the existing stores.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import VirtualMailboxStore
from database.queries import virtual_mailboxes as queries
from database.errors import DatabaseError, QueryError


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if result.get("virtual_mailbox_id") is not None:
        result["virtual_mailbox_id"] = str(result["virtual_mailbox_id"])
    if result.get("owner_user_id") is not None:
        result["owner_user_id"] = str(result["owner_user_id"])
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    # ``account_ids`` is projected as a Postgres ``text[]`` (see the
    # ``_SELECT_FIELDS`` SQL fragment) — psycopg2 surfaces it as a Python
    # list already, but normalise None / non-list inputs to an empty list
    # so the service layer can rely on a flat list[str].
    raw_ids = result.get("account_ids")
    if isinstance(raw_ids, list):
        result["account_ids"] = [str(aid) for aid in raw_ids]
    else:
        result["account_ids"] = []
    # psycopg2 returns JSONB as already-parsed dicts in modern versions,
    # but ``Json`` round-trips (e.g. inside CTEs) sometimes come back as
    # strings. Normalise so the service layer always sees a dict.
    value = result.get("filter_payload")
    if isinstance(value, str):
        try:
            result["filter_payload"] = json.loads(value)
        except (ValueError, TypeError):
            result["filter_payload"] = {}
    elif value is None:
        result["filter_payload"] = {}
    return result


def _scope_payload_for_account_ids(account_ids: list[str]) -> str:
    """JSON-encode the ``{account_ids:[...]}`` wrapper persisted in the
    ``scope_payload`` JSONB column.

    The column name is a historical artifact preserved by migration
    0032; only the ``account_ids`` key inside it is used today.
    """
    return json.dumps({"account_ids": list(account_ids or [])})


def _to_json_param(value: Any) -> str:
    return json.dumps(value or {})


class PgVirtualMailboxStore(VirtualMailboxStore):
    """PostgreSQL-backed virtual mailbox persistence."""

    def create(self, virtual_mailbox: dict[str, Any]) -> dict[str, Any]:
        params = {
            "virtual_mailbox_id": virtual_mailbox["virtual_mailbox_id"],
            "owner_user_id": virtual_mailbox["owner_user_id"],
            "display_name": virtual_mailbox["display_name"],
            "scope_payload": _scope_payload_for_account_ids(
                virtual_mailbox.get("account_ids") or [],
            ),
            "filter_payload": _to_json_param(virtual_mailbox.get("filter_payload")),
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.INSERT_VIRTUAL_MAILBOX, params)
                    row = cur.fetchone()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to create virtual mailbox.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected virtual mailbox create error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            raise QueryError("Virtual mailbox INSERT returned no row.")
        return _row_to_dict(row)

    def get(self, virtual_mailbox_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_VIRTUAL_MAILBOX,
                        {"virtual_mailbox_id": virtual_mailbox_id},
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get virtual mailbox.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected virtual mailbox get error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            return None
        return _row_to_dict(row)

    def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.LIST_VIRTUAL_MAILBOXES_BY_OWNER,
                        {"owner_user_id": owner_user_id},
                    )
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list virtual mailboxes by owner.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected virtual mailbox list error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_row_to_dict(row) for row in rows]

    def update(self, virtual_mailbox: dict[str, Any]) -> dict[str, Any] | None:
        params = {
            "virtual_mailbox_id": virtual_mailbox["virtual_mailbox_id"],
            "display_name": virtual_mailbox["display_name"],
            "scope_payload": _scope_payload_for_account_ids(
                virtual_mailbox.get("account_ids") or [],
            ),
            "filter_payload": _to_json_param(virtual_mailbox.get("filter_payload")),
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.UPDATE_VIRTUAL_MAILBOX, params)
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            # Malformed UUID at the boundary collapses to "no row matched"
            # so the service can surface a clean 404 instead of a 500 —
            # mirrors the same graceful degradation used by ``get`` above.
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update virtual mailbox.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected virtual mailbox update error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            # Race: the row was deleted between the service's ownership
            # pre-check and this UPDATE. Return ``None`` so the service
            # can translate to ``VirtualMailboxNotFound`` (404) — keeps
            # the API surface coherent.
            return None
        return _row_to_dict(row)

    def delete(self, virtual_mailbox_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.DELETE_VIRTUAL_MAILBOX,
                        {"virtual_mailbox_id": virtual_mailbox_id},
                    )
                    return cur.rowcount > 0
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to delete virtual mailbox.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected virtual mailbox delete error ({type(exc).__name__}): {exc}"
            ) from exc


virtual_mailbox_store = PgVirtualMailboxStore()
