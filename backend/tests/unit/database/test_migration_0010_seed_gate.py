from __future__ import annotations

import importlib

import pytest

# The module name starts with a digit, so a plain ``import`` statement is invalid
# syntax; importlib.import_module accepts the dotted string. The versions/ package
# has no __init__.py, but the backend path is on sys.path (root tests/conftest.py).
_MODULE_NAME = "database.migrations.versions.0010_seed_fake_data_for_get_tests"


class _FakeOp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, sql: str) -> None:
        self.calls.append(sql)


@pytest.fixture
def migration_module():
    return importlib.import_module(_MODULE_NAME)


class TestSeedMigrationGate:
    def test_upgrade_skips_seed_when_disabled(self, migration_module, monkeypatch):
        fake_op = _FakeOp()
        monkeypatch.setattr(migration_module, "op", fake_op)
        # upgrade() does the deferred import from database.settings, so patch the
        # source rather than the migration module.
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: False)

        migration_module.upgrade()

        assert fake_op.calls == []

    def test_upgrade_runs_seed_when_enabled(self, migration_module, monkeypatch):
        fake_op = _FakeOp()
        monkeypatch.setattr(migration_module, "op", fake_op)
        monkeypatch.setattr("database.settings.is_test_data_seed_enabled", lambda: True)

        migration_module.upgrade()

        # user, mailboxes, accounts, email_metadata (Gmail), email_metadata (Outlook)
        assert len(fake_op.calls) == 5
