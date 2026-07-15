"""
Unit tests for ``PgImageProxyCacheStore``.

Same pattern as ``test_email_content_repository.py`` /
``test_email_attachment_repository.py``: monkeypatch ``get_connection`` to inject
``FakeCursor``s and assert SQL params, result mapping, error wrapping, and the
``ConnectionPoolError`` propagation invariant.

Deliberately NO ``InvalidTextRepresentation → None`` test: ``url_hash`` is a
SHA-256 hex ``TEXT`` key, not a UUID, so there is no cast that could ever raise
it — testing that guard would verify a branch that does not exist here.
"""

from __future__ import annotations

import psycopg2
import pytest

from database.errors.exceptions import ConnectionPoolError, QueryError
from database.repositories import image_proxy_cache_repository as repo_module
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


_URL_HASH = "a" * 64  # SHA-256 hex


# ── get ────────────────────────────────────────────────────────────


def test_get_happy_path(monkeypatch):
    row = {"content_type": "image/png", "image_bytes": b"PNGDATA"}
    cursor = FakeCursor(fetchone_results=[row])
    patch_connection(monkeypatch, repo_module, [cursor])

    result = repo_module.image_proxy_cache_store.get(_URL_HASH)
    assert result == {"content_type": "image/png", "image_bytes": b"PNGDATA"}
    _sql, params = cursor.executed[0]
    assert params == {"url_hash": _URL_HASH}


def test_get_returns_none_when_no_row(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, repo_module, [cursor])
    assert repo_module.image_proxy_cache_store.get(_URL_HASH) is None


def test_get_null_image_bytes_maps_to_empty_bytes(monkeypatch):
    cursor = FakeCursor(fetchone_results=[{"content_type": "image/png", "image_bytes": None}])
    patch_connection(monkeypatch, repo_module, [cursor])
    result = repo_module.image_proxy_cache_store.get(_URL_HASH)
    assert result == {"content_type": "image/png", "image_bytes": b""}


def test_get_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="Failed to get image proxy cache entry"):
        repo_module.image_proxy_cache_store.get(_URL_HASH)


def test_get_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        repo_module.image_proxy_cache_store.get(_URL_HASH)


def test_get_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool down"))
    with pytest.raises(ConnectionPoolError, match="pool down"):
        repo_module.image_proxy_cache_store.get(_URL_HASH)


# ── upsert ─────────────────────────────────────────────────────────


def test_upsert_happy_path_wraps_binary(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, repo_module, [cursor])

    repo_module.image_proxy_cache_store.upsert(
        _URL_HASH, "https://cdn.example.com/a.png", "image/png", b"PNG",
    )
    assert len(cursor.executed) == 1
    _sql, params = cursor.executed[0]
    assert params["url_hash"] == _URL_HASH
    assert params["url"] == "https://cdn.example.com/a.png"
    assert params["content_type"] == "image/png"
    # The binary is wrapped for parameterised insertion.
    assert isinstance(params["image_bytes"], psycopg2.extensions.Binary)


def test_upsert_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="Failed to upsert image proxy cache entry"):
        repo_module.image_proxy_cache_store.upsert(_URL_HASH, "u", "image/png", b"x")


def test_upsert_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        repo_module.image_proxy_cache_store.upsert(_URL_HASH, "u", "image/png", b"x")


def test_upsert_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool down"))
    with pytest.raises(ConnectionPoolError, match="pool down"):
        repo_module.image_proxy_cache_store.upsert(_URL_HASH, "u", "image/png", b"x")


# ── touch_last_accessed ────────────────────────────────────────────


def test_touch_last_accessed_happy_path(monkeypatch):
    cursor = FakeCursor()
    patch_connection(monkeypatch, repo_module, [cursor])
    repo_module.image_proxy_cache_store.touch_last_accessed(_URL_HASH)
    assert len(cursor.executed) == 1
    _sql, params = cursor.executed[0]
    assert params == {"url_hash": _URL_HASH}


def test_touch_last_accessed_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="Failed to touch image proxy cache last_accessed"):
        repo_module.image_proxy_cache_store.touch_last_accessed(_URL_HASH)


def test_touch_last_accessed_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool down"))
    with pytest.raises(ConnectionPoolError, match="pool down"):
        repo_module.image_proxy_cache_store.touch_last_accessed(_URL_HASH)


# ── purge_expired (returns (count, freed_bytes)) ──────────────────


def test_purge_expired_returns_count_and_freed_bytes(monkeypatch):
    rows = [("h1", 100), ("h2", 200), ("h3", 50)]
    cursor = FakeCursor(fetchall_results=[rows])
    patch_connection(monkeypatch, repo_module, [cursor])
    count, freed = repo_module.image_proxy_cache_store.purge_expired()
    assert count == 3
    assert freed == 350


def test_purge_expired_zero_returns_zero(monkeypatch):
    cursor = FakeCursor(fetchall_results=[[]])
    patch_connection(monkeypatch, repo_module, [cursor])
    count, freed = repo_module.image_proxy_cache_store.purge_expired()
    assert count == 0
    assert freed == 0


def test_purge_expired_psycopg2_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="Failed to purge expired image proxy cache entries"):
        repo_module.image_proxy_cache_store.purge_expired()


def test_purge_expired_generic_raises_query_error(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, repo_module, [cursor])
    with pytest.raises(QueryError, match="RuntimeError"):
        repo_module.image_proxy_cache_store.purge_expired()


def test_purge_expired_propagates_database_error(monkeypatch):
    patch_connection_error(monkeypatch, repo_module, ConnectionPoolError("pool down"))
    with pytest.raises(ConnectionPoolError, match="pool down"):
        repo_module.image_proxy_cache_store.purge_expired()
