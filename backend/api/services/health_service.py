"""
Service layer for the health / readiness check.
"""

from __future__ import annotations

import logging

from database import DatabaseError, warmup_connection
from api.errors.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


def check_readiness() -> dict[str, str]:
    """
    Probe database reachability for the readiness check.

    Returns ``{"status": "ok"}`` when the database answers a ``SELECT 1``;
    raises ``ServiceUnavailableError`` (HTTP 503) otherwise so an orchestrator
    or uptime monitor stops routing traffic to an instance that cannot reach
    its database.
    """
    try:
        warmup_connection()
    except DatabaseError as exc:
        logger.warning("Health readiness DB probe failed (%s): %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            "Readiness check failed: the database is not reachable."
        ) from exc
    except Exception as exc:
        logger.warning("Unexpected health readiness error (%s): %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            "Readiness check failed during the database probe."
        ) from exc
    return {"status": "ok"}
