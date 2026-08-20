"""
Database lifecycle helpers used during app startup.
"""

from __future__ import annotations

import logging

import psycopg2

from database.connection import get_connection
from database.migrations.runner import ensure_schema_at_head
from database.settings import (
    get_database_url,
    get_alembic_ini_path,
    get_token_settings,
    is_startup_auto_migrate_enabled,
)
from database.errors import ConnectionPoolError, DatabaseError, MigrationError, SettingsError

logger = logging.getLogger(__name__)


def run_startup_migrations_if_enabled() -> bool:
    """
    Run Alembic migrations when DB_AUTO_MIGRATE is explicitly enabled.
    """
    if not is_startup_auto_migrate_enabled():
        return False
    try:
        try:
            from alembic import command
            from alembic.config import Config
        except ModuleNotFoundError:
            # Alembic not installed — fall back to the DDL-based runner.
            ensure_schema_at_head(get_database_url())
            return True

        cfg = Config(str(get_alembic_ini_path()))
        # Running EMBEDDED inside the app process: the application already
        # configured logging before uvicorn started, so ``env.py`` must not
        # re-apply ``alembic.ini``'s own logging section over it. Without this
        # flag the startup migration left the whole app mute — see the
        # invariant in ``migrations/env.py``.
        cfg.attributes["configure_logging"] = False
        command.upgrade(cfg, "head")
    except DatabaseError:
        raise
    except Exception as exc:
        raise MigrationError(
            "Failed to run startup database migrations."
        ) from exc
    return True


def warmup_connection() -> None:
    """
    Validate DB reachability without mutating schema.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except DatabaseError:
        raise
    except psycopg2.Error as exc:
        raise ConnectionPoolError(
            "Failed to warm up database connection."
        ) from exc
    except Exception as exc:
        raise ConnectionPoolError(
            f"Unexpected warmup error ({type(exc).__name__}): {exc}"
        ) from exc


def validate_token_encryption_config() -> None:
    """
    Fail fast at startup on an incoherent token-encryption configuration.

    Raises SettingsError when the plaintext fallback is disabled but no
    TOKEN_ENCRYPTION_KEY is configured: such a deployment would refuse every
    token write at the first account connect, so it must not boot. Logs a
    warning (instead of raising) when the legacy plaintext fallback is active
    without a key — the accepted dev/test mode, never for production.
    """
    token_settings = get_token_settings()
    if token_settings.encryption_key is None and not token_settings.plaintext_fallback_enabled:
        raise SettingsError(
            "TOKEN_ENCRYPTION_KEY is required when TOKEN_PLAINTEXT_FALLBACK_ENABLED is false."
        )
    if token_settings.encryption_key is None and token_settings.plaintext_fallback_enabled:
        logger.warning(
            "Token plaintext fallback is enabled without TOKEN_ENCRYPTION_KEY; "
            "OAuth tokens will be stored in plaintext (legacy/dev mode only)."
        )
