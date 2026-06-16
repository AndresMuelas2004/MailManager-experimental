"""
Unit tests for auth.settings (auth settings).
"""

from __future__ import annotations

import pytest

from auth import settings
from auth import AuthSettingsError


def test_get_auth_settings_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "my-client-id")
    monkeypatch.delenv("AUTH_SESSION_LIFETIME_DAYS", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)

    cfg = settings.get_auth_settings()

    assert cfg.google_client_id == "my-client-id"
    assert cfg.session_lifetime_days == 7
    assert cfg.cookie_secure is False
    assert cfg.cookie_samesite == "lax"


def test_get_auth_settings_missing_client_id(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    with pytest.raises(AuthSettingsError, match="GOOGLE_CLIENT_ID"):
        settings.get_auth_settings()


def test_get_auth_settings_custom_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "custom-id")
    monkeypatch.setenv("AUTH_SESSION_LIFETIME_DAYS", "14")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")

    cfg = settings.get_auth_settings()

    assert cfg.google_client_id == "custom-id"
    assert cfg.session_lifetime_days == 14
    assert cfg.cookie_secure is True
    assert cfg.cookie_samesite == "strict"


def test_get_auth_settings_invalid_session_lifetime(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_SESSION_LIFETIME_DAYS", "not-a-number")

    with pytest.raises(AuthSettingsError, match="AUTH_SESSION_LIFETIME_DAYS"):
        settings.get_auth_settings()


def test_get_auth_settings_invalid_cookie_secure(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "maybe")

    with pytest.raises(AuthSettingsError, match="AUTH_COOKIE_SECURE"):
        settings.get_auth_settings()


def test_get_auth_settings_samesite_is_normalised_to_lowercase(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "Strict")

    assert settings.get_auth_settings().cookie_samesite == "strict"


def test_get_auth_settings_invalid_samesite(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "sometimes")

    with pytest.raises(AuthSettingsError, match="AUTH_COOKIE_SAMESITE"):
        settings.get_auth_settings()


def test_get_auth_settings_samesite_none_requires_secure(monkeypatch):
    """SameSite=None without Secure is rejected by browsers, so the config is refused."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    with pytest.raises(AuthSettingsError, match="requires AUTH_COOKIE_SECURE"):
        settings.get_auth_settings()


def test_get_auth_settings_samesite_none_with_secure_is_allowed(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

    cfg = settings.get_auth_settings()
    assert cfg.cookie_samesite == "none"
    assert cfg.cookie_secure is True
