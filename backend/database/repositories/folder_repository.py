"""
PostgreSQL folder repository (carpetas-y-reglas).

Owns the CRUD over ``folders`` plus the ``folder_account_links`` (per-account
provider materialisation) and ``email_folder_members`` (the multi-membership).
Mirrors ``virtual_mailbox_repository`` for the folder CRUD (RealDictCursor,
``_row_to_dict`` normalising UUIDs/timestamps, InvalidTextRepresentation →
None/[]/False, ``except DatabaseError: raise`` before the generic fallbacks).
"""

from __future__ import annotations

from typing import Any

import psycopg2.errors
import psycopg2.extras

from database import connection
from database.contracts import FolderStore
from database.queries import folders as queries
from database.errors import DatabaseError, QueryError


def _folder_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("folder_id", "owner_user_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class PgFolderStore(FolderStore):
    """PostgreSQL-backed persistence for user folders + their membership."""

    # -- folders CRUD -------------------------------------------------------

    def create(self, folder: dict[str, Any]) -> dict[str, Any]:
        params = {
            "folder_id": folder["folder_id"],
            "owner_user_id": folder["owner_user_id"],
            "name": folder["name"],
            "color": folder.get("color"),
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.INSERT_FOLDER, params)
                    row = cur.fetchone()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to create folder.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder create error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            raise QueryError("Folder INSERT returned no row.")
        return _folder_row_to_dict(row)

    def get(self, folder_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.GET_FOLDER, {"folder_id": folder_id})
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get folder.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder get error ({type(exc).__name__}): {exc}"
            ) from exc
        return _folder_row_to_dict(row) if row is not None else None

    def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.LIST_FOLDERS_BY_OWNER,
                        {"owner_user_id": owner_user_id},
                    )
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list folders by owner.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder list error ({type(exc).__name__}): {exc}"
            ) from exc
        return [_folder_row_to_dict(row) for row in rows]

    def update(self, folder: dict[str, Any]) -> dict[str, Any] | None:
        params = {
            "folder_id": folder["folder_id"],
            "name": folder["name"],
            "color": folder.get("color"),
        }
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.UPDATE_FOLDER, params)
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update folder.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder update error ({type(exc).__name__}): {exc}"
            ) from exc
        # Race: row deleted between the service pre-check and the UPDATE — None
        # lets the service surface a clean 404 (mirrors PgVirtualMailboxStore).
        return _folder_row_to_dict(row) if row is not None else None

    def delete(self, folder_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.DELETE_FOLDER, {"folder_id": folder_id})
                    return cur.rowcount > 0
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to delete folder.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder delete error ({type(exc).__name__}): {exc}"
            ) from exc

    # -- folder_account_links ----------------------------------------------

    def get_link(self, folder_id: str, account_id: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.GET_LINK,
                        {"folder_id": folder_id, "account_id": account_id},
                    )
                    row = cur.fetchone()
        except psycopg2.errors.InvalidTextRepresentation:
            return None
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get folder account link.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder link get error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            return None
        result = dict(row)
        for key in ("folder_id", "account_id"):
            if result.get(key) is not None:
                result[key] = str(result[key])
        return result

    def upsert_link(self, folder_id: str, account_id: str, provider_ref: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPSERT_LINK,
                        {
                            "folder_id": folder_id,
                            "account_id": account_id,
                            "provider_ref": provider_ref,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to upsert folder account link.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder link upsert error ({type(exc).__name__}): {exc}"
            ) from exc

    def update_link_ref(self, folder_id: str, account_id: str, provider_ref: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPDATE_LINK_REF,
                        {
                            "folder_id": folder_id,
                            "account_id": account_id,
                            "provider_ref": provider_ref,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to update folder account link ref.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder link ref update error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_links_by_folder(self, folder_id: str) -> list[dict[str, Any]]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.LIST_LINKS_BY_FOLDER, {"folder_id": folder_id})
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list folder account links by folder.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder links list error ({type(exc).__name__}): {exc}"
            ) from exc
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("folder_id", "account_id"):
                if item.get(key) is not None:
                    item[key] = str(item[key])
            result.append(item)
        return result

    def get_ref_map(self, account_id: str) -> dict[str, str]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.LIST_LINKS_BY_ACCOUNT, {"account_id": account_id})
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return {}
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list folder account links by account.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder ref map error ({type(exc).__name__}): {exc}"
            ) from exc
        return {str(row["provider_ref"]): str(row["folder_id"]) for row in rows}

    # -- email_folder_members ----------------------------------------------

    def add_member(self, provider_message_id: str, account_id: str, folder_id: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.ADD_MEMBER,
                        {
                            "provider_message_id": provider_message_id,
                            "account_id": account_id,
                            "folder_id": folder_id,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to add email folder member.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder member add error ({type(exc).__name__}): {exc}"
            ) from exc

    def remove_member(self, provider_message_id: str, account_id: str, folder_id: str) -> bool:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.REMOVE_MEMBER,
                        {
                            "provider_message_id": provider_message_id,
                            "account_id": account_id,
                            "folder_id": folder_id,
                        },
                    )
                    return cur.rowcount > 0
        except psycopg2.errors.InvalidTextRepresentation:
            return False
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to remove email folder member.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder member remove error ({type(exc).__name__}): {exc}"
            ) from exc

    def reconcile_memberships(
        self,
        account_id: str,
        present: list[tuple[str, str]],
        seen_message_ids: list[str],
        managed_folder_ids: list[str],
    ) -> None:
        if not seen_message_ids or not managed_folder_ids:
            return
        present_pmids = [pmid for (pmid, _fid) in present]
        present_folder_ids = [fid for (_pmid, fid) in present]
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    if present:
                        psycopg2.extras.execute_values(
                            cur,
                            queries.UPSERT_MEMBERS_BATCH,
                            [(pmid, account_id, fid) for (pmid, fid) in present],
                            page_size=500,
                        )
                    cur.execute(
                        queries.DELETE_ABSENT_MEMBERS,
                        {
                            "account_id": account_id,
                            "seen_pmids": list(seen_message_ids),
                            "managed_folder_ids": list(managed_folder_ids),
                            "present_pmids": present_pmids,
                            "present_folder_ids": present_folder_ids,
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to reconcile email folder memberships.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder membership reconcile error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_member_message_ids(self, folder_id: str, account_id: str) -> list[str]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.LIST_MEMBER_MESSAGE_IDS_BY_FOLDER_ACCOUNT,
                        {"folder_id": folder_id, "account_id": account_id},
                    )
                    return [str(row[0]) for row in cur.fetchall()]
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list folder member message ids.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folder member ids list error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_folders_for_messages(
        self, pairs: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        if not pairs:
            return []
        pmids = [pmid for (pmid, _aid) in pairs]
        aids = [aid for (_pmid, aid) in pairs]
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        queries.LIST_FOLDERS_FOR_MESSAGES,
                        {"pmids": pmids, "aids": aids},
                    )
                    rows = cur.fetchall()
        except psycopg2.errors.InvalidTextRepresentation:
            return []
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to list folders for messages.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected folders-for-messages error ({type(exc).__name__}): {exc}"
            ) from exc
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("provider_message_id", "account_id", "folder_id"):
                if item.get(key) is not None:
                    item[key] = str(item[key])
            result.append(item)
        return result


folder_store = PgFolderStore()
