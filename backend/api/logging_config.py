"""
Centralised logging configuration.

A single ``dictConfig`` so production logs carry timestamps and honour the
``LOG_LEVEL`` env var (default ``INFO``). ``build_logging_config`` is split out
as a pure function so it can be unit-tested without mutating global logging
state. Applied at the production entrypoint (``main.py``); the dev server uses
uvicorn's own defaults.
"""

from __future__ import annotations

import os
from logging.config import dictConfig

_DEFAULT_LEVEL = "INFO"
_LOG_LEVEL_ENV_VAR = "LOG_LEVEL"


def build_logging_config(level: str) -> dict:
    """
    Return a ``logging.config.dictConfig`` dict that routes the root and uvicorn
    loggers through a single timestamped console handler at *level*.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            name: {"handlers": ["console"], "level": level, "propagate": False}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }


def configure_logging() -> None:
    """Apply the logging configuration, reading the level from ``LOG_LEVEL``."""
    level = os.environ.get(_LOG_LEVEL_ENV_VAR, _DEFAULT_LEVEL).upper()
    dictConfig(build_logging_config(level))
