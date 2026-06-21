from __future__ import annotations

from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from database.repositories import user_repository as user_module
from database.errors.exceptions import ConnectionPoolError, QueryError
from tests.shared.database_fakes import FakeCursor, patch_connection, patch_connection_error


def _fake_row(
    user_id: str = "u1",
    auth_provider: str = "google",
    provider_sub: str = "provider-sub-1",
    email: str = "user@example.com",
    name: str = "Test User",
    avatar_url: str | None = None,
) -> dict:
    return {
        "user_id": user_id,
        "auth_provider": auth_provider,
        "provider_sub": provider_sub,
        "email": email,
        "name": name,
        "avatar_url": avatar_url,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }


def _upsert_payload() -> dict:
    return {
        "user_id": "u1",
        "auth_provider": "google",
        "provider_sub": "gs",
        "email": "a@b.com",
        "name": "N",
        "avatar_url": None,
    }


# ===== upsert =====


def test_upsert_happy_path(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_fake_row()])
    patch_connection(monkeypatch, user_module, [cursor])

    result = user_module.user_store.upsert(_upsert_payload())
    assert result["user_id"] == "u1"
    assert isinstance(result["created_at"], str)


def test_upsert_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="Failed to upsert user"):
        user_module.user_store.upsert(_upsert_payload())


def test_upsert_raises_query_error_on_generic(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("boom"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        user_module.user_store.upsert(_upsert_payload())


def test_upsert_propagates_connection_pool_error(monkeypatch):
    patch_connection_error(monkeypatch, user_module, ConnectionPoolError("pool down"))

    with pytest.raises(ConnectionPoolError, match="pool down"):
        user_module.user_store.upsert(_upsert_payload())


# ===== get_by_id =====


def test_get_by_id_happy_path(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_fake_row()])
    patch_connection(monkeypatch, user_module, [cursor])

    result = user_module.user_store.get_by_id("u1")
    assert result["user_id"] == "u1"
    assert result["email"] == "user@example.com"


def test_get_by_id_returns_none_when_not_found(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.get_by_id("u1") is None


def test_get_by_id_returns_none_on_invalid_text(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.get_by_id("not-a-uuid") is None


def test_get_by_id_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get user"):
        user_module.user_store.get_by_id("u1")


# ===== get_by_email =====


def test_get_by_email_happy_path(monkeypatch):
    cursor = FakeCursor(fetchone_results=[_fake_row(email="dev@example.com")])
    patch_connection(monkeypatch, user_module, [cursor])

    result = user_module.user_store.get_by_email("dev@example.com")
    assert result["email"] == "dev@example.com"
    assert isinstance(result["created_at"], str)


def test_get_by_email_returns_none_when_not_found(monkeypatch):
    cursor = FakeCursor(fetchone_results=[None])
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.get_by_email("missing@example.com") is None


def test_get_by_email_does_not_swallow_invalid_text(monkeypatch):
    """Asymmetry guard: unlike get_by_id/delete, get_by_email does NOT catch
    InvalidTextRepresentation gracefully. The email column is free text (never a
    UUID cast), so swallowing it would only mask a real bug — it must surface as
    QueryError. Documented in database_guide.md; locks the deliberate asymmetry."""
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get user by email"):
        user_module.user_store.get_by_email("dev@example.com")


def test_get_by_email_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="Failed to get user by email"):
        user_module.user_store.get_by_email("dev@example.com")


# ===== delete =====


def test_delete_happy_path_returns_true(monkeypatch):
    cursor = FakeCursor(rowcounts=[1])
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.delete("u1") is True


def test_delete_returns_false_when_not_found(monkeypatch):
    cursor = FakeCursor(rowcounts=[0])
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.delete("u1") is False


def test_delete_returns_false_on_invalid_text(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.errors.InvalidTextRepresentation())
    patch_connection(monkeypatch, user_module, [cursor])

    assert user_module.user_store.delete("not-a-uuid") is False


def test_delete_raises_query_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("fail"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="Failed to delete user"):
        user_module.user_store.delete("u1")


def test_delete_raises_query_error_on_generic(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("unexpected"))
    patch_connection(monkeypatch, user_module, [cursor])

    with pytest.raises(QueryError, match="RuntimeError"):
        user_module.user_store.delete("u1")
