from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool, text


BACKEND_PATH = Path(__file__).resolve().parents[3]
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from database.settings import get_database_url


config = context.config

# LOAD-BEARING for the whole application's observability.
#
# ``alembic.ini`` carries its own ``[logger_root]`` section, and
# ``fileConfig`` defaults to ``disable_existing_loggers=True``. When the app
# runs the startup migration in-process, every logger created before that
# point — ``api.errors.handlers`` (the single place 5xx faults are logged),
# every ``api.services.*`` best-effort swallow point, ``uvicorn.access`` —
# was flipped to ``disabled=True`` and the root level dropped to WARNING.
# The result was an application that logged NOTHING from the moment it
# booted: no 5xx, no swallowed background failures, no access lines.
#
# Two independent guards, both needed:
#  * ``configure_logging`` attribute — the embedded caller
#    (``database.lifecycle``) sets it to False, so the app's own logging
#    configuration survives untouched.
#  * ``disable_existing_loggers=False`` — defence in depth for any other
#    in-process invocation (a CLI run inside an already-configured process,
#    a future caller that forgets the attribute). Alembic's own loggers are
#    named in the file and are configured normally either way.
if config.config_file_name is not None and config.attributes.get(
    "configure_logging", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _database_url() -> str:
    return get_database_url()


def _ensure_alembic_version_table(connection) -> None:
    """
    Pre-create ``alembic_version`` with a wider ``version_num`` column.

    Alembic's default column width is VARCHAR(32), which is too small for
    several migration names in this project (e.g. ``0003_auth_tables_and_mailbox_owner``).
    Creating the table up front with VARCHAR(64) makes Alembic's internal
    ``CREATE TABLE IF NOT EXISTS`` a no-op and keeps the wider column.
    """
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(64) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_alembic_version_table(connection)
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
