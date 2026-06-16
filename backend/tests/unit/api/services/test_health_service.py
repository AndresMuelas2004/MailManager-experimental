"""
Unit tests for the health / readiness service (#11c).

``warmup_connection`` is monkeypatched so the probe never touches a real DB.
"""

from __future__ import annotations

import pytest

from database import DatabaseError
from api.errors.exceptions import ServiceUnavailableError
from api.services import health_service


def test_check_readiness_ok_when_db_reachable(monkeypatch):
    monkeypatch.setattr(health_service, "warmup_connection", lambda: None)
    assert health_service.check_readiness() == {"status": "ok"}


def test_check_readiness_raises_503_on_database_error(monkeypatch):
    def _raise():
        raise DatabaseError("DB unreachable")

    monkeypatch.setattr(health_service, "warmup_connection", _raise)
    with pytest.raises(ServiceUnavailableError, match="database is not reachable"):
        health_service.check_readiness()


def test_check_readiness_raises_503_on_unexpected_error(monkeypatch):
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(health_service, "warmup_connection", _raise)
    with pytest.raises(ServiceUnavailableError, match="during the database probe"):
        health_service.check_readiness()
