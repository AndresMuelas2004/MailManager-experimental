"""
Unit tests for the application factory ``create_app`` — CORS startup guard (H1).
"""

from __future__ import annotations

import pytest

from api.app import create_app


def test_create_app_rejects_wildcard_cors_origin(monkeypatch):
    """A wildcard origin is incompatible with credentialed CORS → fail to boot."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        create_app()


def test_create_app_rejects_wildcard_among_explicit_origins(monkeypatch):
    """A wildcard mixed in with explicit origins is still refused."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com,*")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        create_app()


def test_create_app_accepts_explicit_origins(monkeypatch):
    """Explicit origins build the app without error."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "https://app.example.com,https://admin.example.com"
    )
    app = create_app()
    assert app is not None
