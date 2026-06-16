"""
Health check router.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.services.health_service import check_readiness

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """
    Readiness check: returns {"status": "ok"} (200) when the database is
    reachable, and 503 (via ServiceUnavailableError) otherwise.
    """
    return check_readiness()
