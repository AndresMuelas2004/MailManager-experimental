"""
Unit tests for ``PgVirtualMailboxStore``.

Follows the same pattern as the other repository test modules:
monkeypatch ``get_connection`` to inject :py:class:`FakeCursor`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from database.repositories import virtual_mailbox_repository as vmb_module
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


def _row(**overrides):
    base = {
        "virtual_mailbox_id": "vmb-1",
        "owner_user_id": "user-1",
        "display_name": "Mis favoritos",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"is_favorite": True},
        "created_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ===== create =====


def test_create_happy_path(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_row()])
    patch_connection(monkeypatch, vmb_module, [cursor])

    result = vmb_module.virtual_mailbox_store.create({
        "virtual_mailbox_id": "vmb-1",
        "owner_user_id": "user-1",
        "display_name": "Mis favoritos",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"is_favorite": True},
    })
    assert result["display_name"] == "Mis favoritos"
    assert result["scope_kind"] == "all"
    # The repository must serialise the dict payloads to JSON strings
    # before sending them to psycopg2 (the SQL casts them to JSONB).
    _, params = cursor.executed[0]
    assert isinstance(params["scope_payload"], str)
    assert isinstance(params["filter_payload"], str)


def test_create_returns_normalised_dict_strings_for_ids(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_row()])
    patch_connection(monkeypatch, vmb_module, [cursor])
    result = vmb_module.virtual_mailbox_store.create({
        "virtual_mailbox_id": "vmb-1",
        "owner_user_id": "user-1",
        "display_name": "X",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert isinstance(result["virtual_mailbox_id"], str)
    assert isinstance(result["owner_user_id"], str)
    # created_at / updated_at are stringified for the API layer.
    assert isinstance(result["created_at"], str)
    assert isinstance(result["updated_at"], str)


def test_create_psycopg2_error_wraps(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("db down"))
    patch_connection(monkeypatch, vmb_module, [cursor])
    with pytest.raises(QueryError):
        vmb_module.virtual_mailbox_store.create({
            "virtual_mailbox_id": "vmb-1",
            "owner_user_id": "user-1",
            "display_name": "X",
            "scope_kind": "all",
            "scope_payload": {},
            "filter_payload": {},
        })


# ===== get / list =====


def test_get_returns_dict_when_found(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_row()])
    patch_connection(monkeypatch, vmb_module, [cursor])
    result = vmb_module.virtual_mailbox_store.get("vmb-1")
    assert result is not None
    assert result["virtual_mailbox_id"] == "vmb-1"


def test_get_returns_none_when_missing(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.get("vmb-x") is None


def test_get_invalid_uuid_returns_none(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.get("not-a-uuid") is None


def test_list_by_owner_returns_dicts(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[_row(), _row(virtual_mailbox_id="vmb-2")]])
    patch_connection(monkeypatch, vmb_module, [cursor])
    rows = vmb_module.virtual_mailbox_store.list_by_owner("user-1")
    assert len(rows) == 2
    assert {r["virtual_mailbox_id"] for r in rows} == {"vmb-1", "vmb-2"}


def test_list_by_owner_invalid_uuid_returns_empty(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.list_by_owner("bad") == []


# ===== update =====


def test_update_returns_new_row(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_row(display_name="Renamed")])
    patch_connection(monkeypatch, vmb_module, [cursor])
    result = vmb_module.virtual_mailbox_store.update({
        "virtual_mailbox_id": "vmb-1",
        "display_name": "Renamed",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert result["display_name"] == "Renamed"


def test_update_missing_row_returns_none(monkeypatch):
    """Race: the row was deleted between the service's ownership pre-check
    and this UPDATE. Repository returns ``None`` so the service can
    surface 404 (``VirtualMailboxNotFound``) instead of the old generic
    500 (``QueryError`` → ``VirtualMailboxOperationError``). Keeps the
    GET/UPDATE/DELETE contract symmetric.
    """
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, vmb_module, [cursor])
    result = vmb_module.virtual_mailbox_store.update({
        "virtual_mailbox_id": "vmb-x",
        "display_name": "X",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert result is None


def test_update_invalid_uuid_returns_none(monkeypatch):
    """Malformed UUID at the boundary collapses to "no row matched" so
    the service can surface a clean 404 — mirrors ``get``'s behaviour."""
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, vmb_module, [cursor])
    result = vmb_module.virtual_mailbox_store.update({
        "virtual_mailbox_id": "not-a-uuid",
        "display_name": "X",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert result is None


# ===== delete =====


def test_delete_returns_true_when_row_present(monkeypatch):
    cursor = FakeCursor()
    cursor.rowcount = 1
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.delete("vmb-1") is True


def test_delete_returns_false_when_row_missing(monkeypatch):
    cursor = FakeCursor()
    cursor.rowcount = 0
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.delete("vmb-x") is False


def test_delete_invalid_uuid_returns_false(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, vmb_module, [cursor])
    assert vmb_module.virtual_mailbox_store.delete("bad") is False


def test_delete_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, vmb_module, ConnectionPoolError("pool"))
    with pytest.raises(ConnectionPoolError):
        vmb_module.virtual_mailbox_store.delete("vmb-1")
