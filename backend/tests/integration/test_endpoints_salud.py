"""Tests de integración — endpoint de salud (/health)."""

from __future__ import annotations


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

def test_health(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_returns_503_when_database_unreachable(test_client, monkeypatch):
    """When the readiness DB probe fails, /health surfaces 503 service_unavailable."""
    from api.services import health_service
    from database import DatabaseError

    def _raise():
        raise DatabaseError("DB unreachable")

    monkeypatch.setattr(health_service, "warmup_connection", _raise)
    resp = test_client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"
