"""Tests espejo de ``services_helpers.contenido``: lectura/persistencia de cuerpos y seleccion de prefetch."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.errors.exceptions import ApiError, DatabaseQueryError
from api.services.services_helpers import (
    get_email_content,
    list_unread_recent_uncached,
    persist_email_content,
    purge_expired_email_content,
    touch_email_content_last_accessed,
)
from database import QueryError


# ------------------------------------------------------------------
# get_email_content
# ------------------------------------------------------------------

class TestGetEmailContent:

    def test_happy_path_returns_dict(self):
        fake_row = {"html_body": "<p>hi</p>", "text_body": "hi"}
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.get.return_value = fake_row
            result = get_email_content("acc-1", "m1")
        assert result == fake_row

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.get.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                get_email_content("acc-1", "m1")

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.get.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to read email content"):
                get_email_content("acc-1", "m1")


# ------------------------------------------------------------------
# persist_email_content
# ------------------------------------------------------------------

class TestPersistEmailContent:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            persist_email_content("acc-1", "m1", "<p>hi</p>", "hi")
        mock_store.upsert.assert_called_once_with("acc-1", "m1", "<p>hi</p>", "hi")

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.upsert.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                persist_email_content("acc-1", "m1", None, None)

    def test_generic_exception_raises_api_error(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.upsert.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to persist email content"):
                persist_email_content("acc-1", "m1", None, None)


# ------------------------------------------------------------------
# touch_email_content_last_accessed (sliding TTL refresh on cache hit)
# ------------------------------------------------------------------
# Best-effort by design: a failure to bump the TTL must NEVER affect the
# content response — the body is already served from cache. So unlike the
# translation helpers below, EVERY error is swallowed (logged, not raised).

class TestTouchEmailContentLastAccessed:

    def test_happy_path_calls_store(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            touch_email_content_last_accessed("acc-1", "m1")
        mock_store.touch_last_accessed.assert_called_once_with("acc-1", "m1")

    def test_database_error_swallowed(self):
        # A DatabaseError must NOT propagate — the read already succeeded.
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.touch_last_accessed.side_effect = QueryError("DB fail")
            # No exception escapes.
            assert touch_email_content_last_accessed("acc-1", "m1") is None

    def test_generic_exception_swallowed(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.touch_last_accessed.side_effect = RuntimeError("boom")
            assert touch_email_content_last_accessed("acc-1", "m1") is None


# ------------------------------------------------------------------
# purge_expired_email_content (post-sync TTL eviction, best-effort)
# ------------------------------------------------------------------
# Runs in the post-sync background task and must never raise. Returns the
# rowcount on success, or 0 on any error (the next sync retries).

class TestPurgeExpiredEmailContent:

    def test_happy_path_returns_rowcount(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.return_value = 4
            result = purge_expired_email_content(["acc-1", "acc-2"])
        assert result == 4
        mock_store.purge_expired_for_accounts.assert_called_once_with(["acc-1", "acc-2"])

    def test_database_error_returns_zero(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.side_effect = QueryError("DB fail")
            assert purge_expired_email_content(["acc-1"]) == 0

    def test_generic_exception_returns_zero(self):
        with patch("api.services.services_helpers.contenido.email_content_store") as mock_store:
            mock_store.purge_expired_for_accounts.side_effect = RuntimeError("boom")
            assert purge_expired_email_content(["acc-1"]) == 0


# ------------------------------------------------------------------
# list_unread_recent_uncached (content-prefetch target selection)
# ------------------------------------------------------------------
# Thin translation wrapper that CAN raise (the prefetch caller wraps it in
# its own best-effort try/except), so it follows the standard translation
# pattern rather than swallowing.

class TestListUnreadRecentUncached:

    def test_happy_path_returns_ids(self):
        with patch("api.services.services_helpers.contenido.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.return_value = ["m3", "m1"]
            result = list_unread_recent_uncached("acc-1", 50)
        assert result == ["m3", "m1"]
        mock_store.list_unread_recent_uncached.assert_called_once_with("acc-1", 50)

    def test_database_error_translated(self):
        with patch("api.services.services_helpers.contenido.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.side_effect = QueryError("DB fail")
            with pytest.raises(DatabaseQueryError):
                list_unread_recent_uncached("acc-1", 50)

    def test_generic_exception_raises_fallback(self):
        with patch("api.services.services_helpers.contenido.email_metadata_store") as mock_store:
            mock_store.list_unread_recent_uncached.side_effect = RuntimeError("boom")
            with pytest.raises(ApiError, match="Failed to list unread recent uncached messages"):
                list_unread_recent_uncached("acc-1", 50)
