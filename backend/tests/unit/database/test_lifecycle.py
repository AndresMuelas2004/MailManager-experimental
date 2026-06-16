from __future__ import annotations

import contextlib
from collections.abc import Iterator

import psycopg2
import pytest

from database import lifecycle as lifecycle_module
from database.errors.exceptions import ConnectionPoolError, MigrationError, SettingsError
from tests.shared.database_fakes import FakeCursor


def test_warmup_connection_success(monkeypatch):
    """Happy path: SELECT 1 succeeds without error."""
    cursor = FakeCursor()

    class _FakeConn:
        def cursor(self, *a, **kw):
            return cursor

    @contextlib.contextmanager
    def _get_connection() -> Iterator[_FakeConn]:
        yield _FakeConn()

    monkeypatch.setattr(lifecycle_module, "get_connection", _get_connection)
    lifecycle_module.warmup_connection()  # should not raise
    assert len(cursor.executed) == 1
    assert cursor.executed[0][0] == "SELECT 1"


def test_warmup_raises_connection_pool_error_on_psycopg2(monkeypatch):
    cursor = FakeCursor(execute_side_effect=psycopg2.OperationalError("connection refused"))

    class _FakeConn:
        def cursor(self, *a, **kw):
            return cursor

    @contextlib.contextmanager
    def _get_connection() -> Iterator[_FakeConn]:
        yield _FakeConn()

    monkeypatch.setattr(lifecycle_module, "get_connection", _get_connection)

    with pytest.raises(ConnectionPoolError, match="Failed to warm up"):
        lifecycle_module.warmup_connection()


def test_warmup_raises_connection_pool_error_on_generic(monkeypatch):
    cursor = FakeCursor(execute_side_effect=RuntimeError("unexpected"))

    class _FakeConn:
        def cursor(self, *a, **kw):
            return cursor

    @contextlib.contextmanager
    def _get_connection() -> Iterator[_FakeConn]:
        yield _FakeConn()

    monkeypatch.setattr(lifecycle_module, "get_connection", _get_connection)

    with pytest.raises(ConnectionPoolError, match="RuntimeError"):
        lifecycle_module.warmup_connection()


def test_migration_failure_raises_migration_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("DB_AUTO_MIGRATE", "true")
    monkeypatch.setattr(
        lifecycle_module,
        "ensure_schema_at_head",
        lambda dsn: (_ for _ in ()).throw(RuntimeError("migration boom")),
    )

    import sys
    monkeypatch.setitem(sys.modules, "alembic", None)
    monkeypatch.setitem(sys.modules, "alembic.command", None)
    monkeypatch.setitem(sys.modules, "alembic.config", None)

    with pytest.raises(MigrationError, match="Failed to run startup database migrations"):
        lifecycle_module.run_startup_migrations_if_enabled()


# ===== validate_token_encryption_config (fail-closed startup guard) =====


def test_validate_token_encryption_config_raises_without_key_and_without_fallback(monkeypatch):
    """A deploy with no key and the plaintext fallback disabled must not boot."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")

    with pytest.raises(SettingsError, match="TOKEN_ENCRYPTION_KEY is required"):
        lifecycle_module.validate_token_encryption_config()


def test_validate_token_encryption_config_allows_plaintext_fallback_without_key(monkeypatch):
    """Legacy/dev mode: no key but fallback explicitly enabled is allowed (warns, no raise)."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "true")

    lifecycle_module.validate_token_encryption_config()  # must not raise


def test_validate_token_encryption_config_allows_configured_key(monkeypatch):
    """A configured key satisfies the guard regardless of the fallback flag."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "any-non-empty-key")
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")

    lifecycle_module.validate_token_encryption_config()  # must not raise
