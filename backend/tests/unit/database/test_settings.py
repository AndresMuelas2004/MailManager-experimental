from __future__ import annotations

from pathlib import Path

import pytest

from database import settings
from database.errors.exceptions import SettingsError


def test_get_database_settings_uses_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/mailmanager")
    monkeypatch.delenv("DB_POOL_MIN_CONN", raising=False)
    monkeypatch.delenv("DB_POOL_MAX_CONN", raising=False)
    monkeypatch.delenv("DB_CONNECT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DB_APPLICATION_NAME", raising=False)

    cfg = settings.get_database_settings()

    assert cfg.pool_min_conn == 1
    # Raised 10 -> 25 to cover the parallel background backfill's DB-write
    # semaphore (BACKFILL_DB_WRITE_CONCURRENCY, default 8) plus request headroom.
    assert cfg.pool_max_conn == 25
    assert cfg.connect_timeout_seconds == 10
    assert cfg.application_name == "mailmanager-api"


def test_get_database_settings_validates_pool_range(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/mailmanager")
    monkeypatch.setenv("DB_POOL_MIN_CONN", "12")
    monkeypatch.setenv("DB_POOL_MAX_CONN", "5")

    with pytest.raises(SettingsError):
        settings.get_database_settings()


def test_token_plaintext_fallback_parses_boolean(monkeypatch):
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")
    assert settings.is_token_plaintext_fallback_enabled() is False


def test_token_plaintext_fallback_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "sometimes")

    with pytest.raises(SettingsError):
        settings.is_token_plaintext_fallback_enabled()


# ===== get_database_url =====


def test_get_database_url_missing_raises_settings_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        settings.get_database_url()


def test_get_database_url_happy_path(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://host/db")
    assert settings.get_database_url() == "postgresql://host/db"


# ===== get_token_encryption_key =====


def test_get_token_encryption_key_required_missing_raises(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(SettingsError, match="TOKEN_ENCRYPTION_KEY"):
        settings.get_token_encryption_key(required=True)


def test_get_token_encryption_key_optional_missing_returns_none(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    assert settings.get_token_encryption_key(required=False) is None


def test_get_token_encryption_key_present(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "my-key")
    assert settings.get_token_encryption_key(required=True) == "my-key"


# ===== get_provider_credentials_path =====


def test_get_provider_credentials_path_unknown_provider():
    assert settings.get_provider_credentials_path("unknown") is None


def test_get_provider_credentials_path_missing_env(monkeypatch):
    monkeypatch.delenv("MIA_GMAIL_CREDENTIALS_PATH", raising=False)
    with pytest.raises(SettingsError, match="MIA_GMAIL_CREDENTIALS_PATH"):
        settings.get_provider_credentials_path("gmail")


def test_get_provider_credentials_path_happy_path(monkeypatch):
    monkeypatch.setenv("MIA_GMAIL_CREDENTIALS_PATH", "/path/to/creds.json")
    assert settings.get_provider_credentials_path("gmail") == "/path/to/creds.json"


# ===== get_token_settings =====


class TestGetTokenSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY_ID", raising=False)
        monkeypatch.delenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", raising=False)
        ts = settings.get_token_settings()
        assert ts.encryption_key is None
        assert ts.encryption_key_id == "v1"
        assert ts.plaintext_fallback_enabled is False

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "my-key")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY_ID", "k42")
        monkeypatch.setenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", "false")
        ts = settings.get_token_settings()
        assert ts.encryption_key == "my-key"
        assert ts.encryption_key_id == "k42"
        assert ts.plaintext_fallback_enabled is False

    def test_strips_whitespace_from_key(self, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "  key  ")
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY_ID", raising=False)
        monkeypatch.delenv("TOKEN_PLAINTEXT_FALLBACK_ENABLED", raising=False)
        ts = settings.get_token_settings()
        assert ts.encryption_key == "key"


# ===== is_startup_auto_migrate_enabled =====


class TestIsStartupAutoMigrateEnabled:
    def test_default_false(self, monkeypatch):
        monkeypatch.delenv("DB_AUTO_MIGRATE", raising=False)
        assert settings.is_startup_auto_migrate_enabled() is False

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("DB_AUTO_MIGRATE", "true")
        assert settings.is_startup_auto_migrate_enabled() is True


# ===== is_test_data_seed_enabled =====


class TestIsTestDataSeedEnabled:
    def test_default_true_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_SEED_TEST_DATA", raising=False)
        assert settings.is_test_data_seed_enabled() is True

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("DB_SEED_TEST_DATA", "false")
        assert settings.is_test_data_seed_enabled() is False

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("DB_SEED_TEST_DATA", "true")
        assert settings.is_test_data_seed_enabled() is True

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("DB_SEED_TEST_DATA", "quizas")
        with pytest.raises(SettingsError):
            settings.is_test_data_seed_enabled()


# ===== get_alembic_ini_path =====


class TestGetAlembicIniPath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("DB_ALEMBIC_INI_PATH", raising=False)
        result = settings.get_alembic_ini_path()
        assert str(result).endswith("database/alembic.ini") or str(result).endswith("database\\alembic.ini")

    def test_custom_path(self, monkeypatch):
        monkeypatch.setenv("DB_ALEMBIC_INI_PATH", "/custom/alembic.ini")
        assert settings.get_alembic_ini_path() == Path("/custom/alembic.ini")


# ===== get_frontend_origin =====


class TestGetFrontendOrigin:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        assert settings.get_frontend_origin() == "http://localhost:5173"

    def test_uses_first_origin(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.example,https://b.example")
        assert settings.get_frontend_origin() == "https://a.example"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "  https://a.example , https://b.example  ")
        assert settings.get_frontend_origin() == "https://a.example"

    def test_empty_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
        assert settings.get_frontend_origin() == "http://localhost:5173"
