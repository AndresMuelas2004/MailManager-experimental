from __future__ import annotations

import psycopg2
import pytest

from database.errors import MigrationError, SettingsError
from database.migrations import runner
from tests.shared.database_fakes import FakeCursor


def _executed_sql(cur: FakeCursor) -> list[str]:
    return [sql for sql, _params in cur.executed]


class _FakeConn:
    """Minimal psycopg2-connection stand-in: context manager + cursor()."""

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> FakeCursor:
        return FakeCursor()


# ===== _apply_statements seed gate =====


class TestApplyStatementsSeedGate:
    def test_includes_seed_when_enabled(self, monkeypatch):
        # The helper is imported inside _apply_statements (deferred import), so the
        # binding resolves against database.settings on every call: patch the source.
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: True)
        cur = FakeCursor()

        runner._apply_statements(cur)

        executed = _executed_sql(cur)
        assert any("inventadoParaEndpointGet" in sql for sql in executed)
        assert any("INSERT INTO email_metadata" in sql for sql in executed)

    def test_skips_seed_when_disabled(self, monkeypatch):
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: False)
        cur = FakeCursor()

        runner._apply_statements(cur)

        executed = _executed_sql(cur)
        # Seed data must be absent...
        assert not any("inventadoParaEndpointGet" in sql for sql in executed)
        assert not any("INSERT INTO email_metadata" in sql for sql in executed)
        # ...but the schema and the alembic_version stamps must still run.
        assert any("CREATE TABLE IF NOT EXISTS users" in sql for sql in executed)
        assert any("INSERT INTO alembic_version" in sql for sql in executed)

    def test_enabled_runs_schema_plus_seed_statements(self, monkeypatch):
        # Guards the _DDL_STATEMENTS / _SEED_STATEMENTS split: a schema statement
        # that accidentally lands in _SEED_STATEMENTS would break this count.
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: True)
        cur = FakeCursor()

        runner._apply_statements(cur)

        assert len(cur.executed) == len(runner._DDL_STATEMENTS) + len(runner._SEED_STATEMENTS)

    def test_disabled_runs_only_schema_statements(self, monkeypatch):
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: False)
        cur = FakeCursor()

        runner._apply_statements(cur)

        assert len(cur.executed) == len(runner._DDL_STATEMENTS)

    def test_seed_runs_after_all_schema_when_enabled(self, monkeypatch):
        # Order is load-bearing (database_guide.md): _SEED_STATEMENTS must run
        # AFTER every _DDL_STATEMENTS entry — the seed INSERTs only list
        # pre-0010 columns and need the tables to exist first. A count/membership
        # check stays green if the two lists are concatenated in the wrong order;
        # this pins the order explicitly.
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: True)
        cur = FakeCursor()

        runner._apply_statements(cur)

        executed = _executed_sql(cur)
        ddl_count = len(runner._DDL_STATEMENTS)
        assert executed[:ddl_count] == runner._DDL_STATEMENTS
        assert executed[ddl_count:] == runner._SEED_STATEMENTS


class TestEnsureSchemaAtHead:
    def test_settings_error_propagates_without_double_wrap(self, monkeypatch):
        # A SettingsError (DatabaseError subclass) raised by the seed-gate flag
        # read inside _apply_statements must propagate AS-IS, not be re-wrapped
        # as MigrationError by the generic `except Exception`
        # (database/CLAUDE.md §7: never double-wrap DatabaseError).
        monkeypatch.setattr(runner.psycopg2, "connect", lambda *a, **k: _FakeConn())
        monkeypatch.setenv("DB_SEED_TEST_DATA", "not-a-bool")

        with pytest.raises(SettingsError):
            runner.ensure_schema_at_head("postgresql://localhost/db")

    def test_psycopg2_error_is_translated_to_migration_error(self, monkeypatch):
        def _boom(*a, **k):
            raise psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(runner.psycopg2, "connect", _boom)

        with pytest.raises(MigrationError):
            runner.ensure_schema_at_head("postgresql://localhost/db")

    def test_unexpected_error_is_translated_to_migration_error(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(runner.psycopg2, "connect", _boom)

        with pytest.raises(MigrationError, match="Unexpected migration error"):
            runner.ensure_schema_at_head("postgresql://localhost/db")
