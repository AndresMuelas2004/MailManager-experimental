"""Unit tests for ``api.services.backfill_config`` — env parsing with silent
invalid-value fallback and the worker kill-switch. Pure config reads, no DB.

Every var is cleared before each test so a stray real env var can't leak in and
flip an assertion. The silent-fallback behaviour is load-bearing: a malformed
value must default (and log), never raise, or a typo in one env var would strand
the whole worker with no test signal.
"""

from __future__ import annotations

import pytest

from api.services import backfill_config


_ALL_VARS = [
    "BACKFILL_WORKER_ENABLED",
    "BACKFILL_MAX_EMAILS_PER_ACCOUNT",
    "BACKFILL_MAX_CONCURRENT",
    "BACKFILL_DB_WRITE_CONCURRENCY",
    "BACKFILL_MAX_ATTEMPTS",
    "BACKFILL_GMAIL_GETS_PER_MINUTE",
    "BACKFILL_OUTLOOK_PAGE_DELAY_MS",
    "BACKFILL_POLL_INTERVAL_S",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


# ===== is_backfill_worker_enabled (kill-switch, default ON) =====


class TestWorkerEnabled:
    def test_unset_defaults_to_enabled(self):
        assert backfill_config.is_backfill_worker_enabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On", "  true  "])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", value)
        assert backfill_config.is_backfill_worker_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "garbage"])
    def test_non_truthy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("BACKFILL_WORKER_ENABLED", value)
        assert backfill_config.is_backfill_worker_enabled() is False


# ===== int accessors (default + override + invalid fallback) =====


class TestIntAccessors:
    @pytest.mark.parametrize(
        "func, default",
        [
            (backfill_config.backfill_max_emails_per_account, 100000),
            # Raised 2 -> 15 (aligned with MAX_ACCOUNTS_PER_USER) so every account
            # a user connects downloads its history in parallel.
            (backfill_config.backfill_max_concurrent, 15),
            # DB-write semaphore bound, kept safely below DB_POOL_MAX_CONN (25).
            (backfill_config.backfill_db_write_concurrency, 8),
            # Auto-retry budget consumed by the reaper before a job stays failed.
            (backfill_config.backfill_max_attempts, 5),
            (backfill_config.backfill_gmail_gets_per_minute, 300),
            (backfill_config.backfill_outlook_page_delay_ms, 300),
        ],
    )
    def test_defaults_when_unset(self, func, default):
        assert func() == default

    def test_valid_override_is_read(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_MAX_EMAILS_PER_ACCOUNT", "25000")
        assert backfill_config.backfill_max_emails_per_account() == 25000

    @pytest.mark.parametrize(
        "env, func, override",
        [
            ("BACKFILL_DB_WRITE_CONCURRENCY", backfill_config.backfill_db_write_concurrency, 12),
            ("BACKFILL_MAX_ATTEMPTS", backfill_config.backfill_max_attempts, 9),
        ],
    )
    def test_new_int_overrides_are_read(self, monkeypatch, env, func, override):
        monkeypatch.setenv(env, str(override))
        assert func() == override

    def test_invalid_value_falls_back_without_raising(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_MAX_CONCURRENT", "not-a-number")
        # Must NOT raise — a bad env var would otherwise strand the worker.
        assert backfill_config.backfill_max_concurrent() == 15

    def test_blank_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_GMAIL_GETS_PER_MINUTE", "   ")
        assert backfill_config.backfill_gmail_gets_per_minute() == 300


# ===== float accessor =====


class TestPollInterval:
    def test_default_when_unset(self):
        assert backfill_config.backfill_poll_interval_s() == 5.0

    def test_valid_override_is_read(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_POLL_INTERVAL_S", "1.5")
        assert backfill_config.backfill_poll_interval_s() == 1.5

    def test_invalid_value_falls_back_without_raising(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_POLL_INTERVAL_S", "abc")
        assert backfill_config.backfill_poll_interval_s() == 5.0
