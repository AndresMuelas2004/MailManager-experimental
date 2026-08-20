from __future__ import annotations

import contextlib
import logging
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


def test_run_startup_migrations_returns_false_when_disabled(monkeypatch):
    """DB_AUTO_MIGRATE off is the default prod boot path: a no-op returning False."""
    monkeypatch.setenv("DB_AUTO_MIGRATE", "false")
    assert lifecycle_module.run_startup_migrations_if_enabled() is False


def test_run_startup_migrations_propagates_database_error_unwrapped(monkeypatch):
    """A DatabaseError must propagate as-is, never re-wrapped as a generic MigrationError."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("DB_AUTO_MIGRATE", "true")
    monkeypatch.setattr(
        lifecycle_module,
        "ensure_schema_at_head",
        lambda dsn: (_ for _ in ()).throw(ConnectionPoolError("pool down during migration")),
    )

    import sys
    monkeypatch.setitem(sys.modules, "alembic", None)
    monkeypatch.setitem(sys.modules, "alembic.command", None)
    monkeypatch.setitem(sys.modules, "alembic.config", None)

    with pytest.raises(ConnectionPoolError, match="pool down during migration"):
        lifecycle_module.run_startup_migrations_if_enabled()


# ===== validate_token_encryption_config (fail-closed startup guard) =====


def test_validate_token_encryption_config_raises_without_key_and_without_fallback(monkeypatch):
    """A deploy with no key and the plaintext fallback disabled must not boot."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")

    with pytest.raises(SettingsError, match="TOKEN_ENCRYPTION_KEY is required"):
        lifecycle_module.validate_token_encryption_config()


def test_validate_token_encryption_config_allows_plaintext_fallback_without_key(monkeypatch, caplog):
    """Legacy/dev mode: no key but fallback explicitly enabled is allowed (warns, no raise)."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "true")

    with caplog.at_level(logging.WARNING, logger="database.lifecycle"):
        lifecycle_module.validate_token_encryption_config()  # must not raise

    # The emitted warning is the only observable signal of the fail-open dev mode;
    # a regression that silences it must turn this test red.
    assert any(
        r.levelno == logging.WARNING and "plaintext" in r.getMessage()
        for r in caplog.records
    ), "expected a WARNING that the plaintext fallback is active without a key"


def test_validate_token_encryption_config_allows_configured_key(monkeypatch):
    """A configured key satisfies the guard regardless of the fallback flag."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "any-non-empty-key")
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")

    lifecycle_module.validate_token_encryption_config()  # must not raise


# ---------------------------------------------------------------------------
# Startup migration must not destroy the application's logging configuration
# ---------------------------------------------------------------------------


def test_startup_migration_tells_alembic_not_to_configure_logging(monkeypatch):
    """The single guard that keeps the app observable in production.

    ``alembic.ini`` carries its own ``[logger_root]`` section and Alembic's
    ``env.py`` feeds it to ``fileConfig``, whose default
    ``disable_existing_loggers=True`` flips every already-created logger to
    ``disabled``. Run in-process at startup, that silenced the WHOLE app from
    the moment it booted — no 5xx from ``api.errors.handlers``, no swallowed
    background failures, no ``uvicorn.access`` lines. The embedded caller must
    therefore hand Alembic ``configure_logging=False``.
    """
    captured: dict = {}

    class _FakeConfig:
        def __init__(self, path):
            self.path = path
            self.attributes: dict = {}

    def _fake_upgrade(cfg, revision):
        captured["attributes"] = dict(cfg.attributes)
        captured["revision"] = revision

    import sys
    import types

    fake_command = types.ModuleType("alembic.command")
    fake_command.upgrade = _fake_upgrade
    fake_config_mod = types.ModuleType("alembic.config")
    fake_config_mod.Config = _FakeConfig
    fake_alembic = types.ModuleType("alembic")
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)
    monkeypatch.setitem(sys.modules, "alembic.config", fake_config_mod)
    monkeypatch.setattr(lifecycle_module, "is_startup_auto_migrate_enabled", lambda: True)
    monkeypatch.setattr(lifecycle_module, "get_alembic_ini_path", lambda: "alembic.ini")

    assert lifecycle_module.run_startup_migrations_if_enabled() is True
    assert captured["revision"] == "head"
    assert captured["attributes"]["configure_logging"] is False


def test_alembic_ini_file_config_does_not_disable_app_loggers():
    """Defence in depth: even when ``env.py`` DOES apply ``alembic.ini`` (the
    CLI path), passing ``disable_existing_loggers=False`` keeps the app's
    loggers alive. Without it a CLI run inside a configured process silences
    the caller."""
    from logging.config import fileConfig

    from database.settings import get_alembic_ini_path

    app_logger = logging.getLogger("api.errors.handlers")
    uvicorn_logger = logging.getLogger("uvicorn.access")
    app_logger.disabled = False
    uvicorn_logger.disabled = False

    fileConfig(str(get_alembic_ini_path()), disable_existing_loggers=False)

    assert app_logger.disabled is False
    assert uvicorn_logger.disabled is False
