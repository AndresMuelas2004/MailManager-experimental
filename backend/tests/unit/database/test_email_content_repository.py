"""
Unit tests for PgEmailContentStore.

Follows the same pattern as test_email_metadata_repository.py:
monkeypatch get_connection to inject FakeCursors.
"""

from __future__ import annotations

import psycopg2
import psycopg2.errors
import pytest

from database.repositories import email_content_repository as ec_module
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


# ===== get =====


def test_get_happy_path(monkeypatch):
    row = {"html_body": "<p>hello</p>", "text_body": "hello", "fetched_at": "2024-01-01"}
    cursor = FakeCursor(fetchone_results=[row])
    patch_connection(monkeypatch, ec_module, [cursor])

    result = ec_module.email_content_store.get("acc1", "m1")
    assert result is not None
    assert result["html_body"] == "<p>hello</p>"
    assert result["text_body"] == "hello"
    assert len(cursor.executed) == 1


def test_get_returns_none_when_no_row(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, ec_module, [cursor])

    result = ec_module.email_content_store.get("acc1", "m1")
    assert result is None


def test_get_invalid_uuid_returns_none(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, ec_module, [cursor])

    result = ec_module.email_content_store.get("not-a-uuid", "m1")
    assert result is None


def test_get_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get email content"):
        ec_module.email_content_store.get("acc1", "m1")


def test_get_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        ec_module.email_content_store.get("acc1", "m1")


def test_get_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, ec_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        ec_module.email_content_store.get("acc1", "m1")


# ===== upsert =====


def test_upsert_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, ec_module, [cursor])

    ec_module.email_content_store.upsert("acc1", "m1", "<p>hi</p>", "hi")
    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert params["account_id"] == "acc1"
    assert params["provider_message_id"] == "m1"
    assert params["html_body"] == "<p>hi</p>"
    assert params["text_body"] == "hi"


def test_upsert_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="Failed to upsert email content"):
        ec_module.email_content_store.upsert("acc1", "m1", None, None)


def test_upsert_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        ec_module.email_content_store.upsert("acc1", "m1", None, None)


def test_upsert_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, ec_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        ec_module.email_content_store.upsert("acc1", "m1", None, None)


# ===== touch_last_accessed (sliding TTL refresh on cache hit) =====


def test_touch_last_accessed_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, ec_module, [cursor])

    ec_module.email_content_store.touch_last_accessed("acc1", "m1")
    assert len(cursor.executed) == 1
    _sql, params = cursor.executed[0]
    assert params == {"account_id": "acc1", "provider_message_id": "m1"}


def test_touch_last_accessed_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="Failed to touch email content last_accessed"):
        ec_module.email_content_store.touch_last_accessed("acc1", "m1")


def test_touch_last_accessed_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        ec_module.email_content_store.touch_last_accessed("acc1", "m1")


def test_touch_last_accessed_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, ec_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        ec_module.email_content_store.touch_last_accessed("acc1", "m1")


# ===== purge_expired_for_accounts (per-account TTL eviction) =====


def test_purge_expired_for_accounts_returns_rowcount(monkeypatch):
    cursor = FakeCursor(rowcounts=[7])
    patch_connection(monkeypatch, ec_module, [cursor])

    result = ec_module.email_content_store.purge_expired_for_accounts(["acc1", "acc2"])
    assert result == 7
    sql, params = cursor.executed[0]
    assert params == {"account_ids": ["acc1", "acc2"]}
    # The account_ids array MUST carry the ::uuid[] cast: psycopg2 adapts a
    # Python list[str] as text[], and "account_id = ANY(%(account_ids)s)"
    # without the cast raises "operator does not exist: uuid = text" on the
    # first non-empty purge against real PostgreSQL (FakeCursor never runs the
    # SQL, so the string itself is the only regression guard here).
    assert "%(account_ids)s::uuid[]" in sql


def test_purge_expired_for_accounts_empty_returns_zero_without_db(monkeypatch):
    # An empty account list must short-circuit BEFORE touching the connection —
    # ``ANY('{}')`` would scan nothing anyway, but the guard avoids a pointless
    # round trip in the post-sync background task.
    def _explode():
        raise AssertionError("get_connection must not be called for empty account_ids")

    monkeypatch.setattr(ec_module.connection, "get_connection", _explode)
    assert ec_module.email_content_store.purge_expired_for_accounts([]) == 0


def test_purge_expired_for_accounts_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="Failed to purge expired email content"):
        ec_module.email_content_store.purge_expired_for_accounts(["acc1"])


def test_purge_expired_for_accounts_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, ec_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        ec_module.email_content_store.purge_expired_for_accounts(["acc1"])


def test_purge_expired_for_accounts_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, ec_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        ec_module.email_content_store.purge_expired_for_accounts(["acc1"])
