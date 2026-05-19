"""
Unit tests for the attachment-related extensions in ``services_helpers``:

- ``recompute_has_attachments`` — DB error wrapping + happy-path delegation.
- ``translate_core_error`` — new mappings for the attachment CoreError
  hierarchy: 502 (provider_forbidden) vs 503 (provider_unavailable),
  blocked extension, and send-failed.
"""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    ApiError,
    AttachmentBlockedExtension,
    AttachmentProviderForbidden,
    AttachmentProviderUnavailable,
    AttachmentSendFailed,
    AttachmentTooLarge,
    AttachmentUnavailable,
    DatabaseQueryError,
)
from api.services import services_helpers
from core.email.errors import (
    EmailAttachmentBlockedByProvider,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
)
from database.errors import QueryError as DbQueryError


# ── recompute_has_attachments ──────────────────────────────────────


class TestRecomputeHasAttachments:

    def test_happy_path_delegates_to_store(self, monkeypatch):
        calls: list[tuple] = []
        monkeypatch.setattr(
            services_helpers.email_metadata_store, "update_has_attachments",
            lambda aid, mid: calls.append((aid, mid)),
        )
        services_helpers.recompute_has_attachments("acc-1", "msg-1")
        assert calls == [("acc-1", "msg-1")]

    def test_db_error_translated(self, monkeypatch):
        monkeypatch.setattr(
            services_helpers.email_metadata_store, "update_has_attachments",
            lambda *_a, **_kw: (_ for _ in ()).throw(DbQueryError("db down")),
        )
        with pytest.raises(DatabaseQueryError):
            services_helpers.recompute_has_attachments("acc-1", "msg-1")

    def test_unexpected_error_uses_fallback(self, monkeypatch):
        monkeypatch.setattr(
            services_helpers.email_metadata_store, "update_has_attachments",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(ApiError):
            services_helpers.recompute_has_attachments("acc-1", "msg-1")

    def test_explicit_fallback_class_used(self, monkeypatch):
        class _Custom(ApiError):
            code = "custom"

        monkeypatch.setattr(
            services_helpers.email_metadata_store, "update_has_attachments",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(_Custom):
            services_helpers.recompute_has_attachments(
                "acc-1", "msg-1", fallback=_Custom,
            )


# ── translate_core_error — attachment cases ────────────────────────


class TestTranslateCoreErrorAttachments:

    def test_email_attachment_not_found_maps_to_attachment_unavailable(self):
        exc = EmailAttachmentNotFound("gone")
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentUnavailable)

    def test_blocked_by_provider_maps_to_blocked_extension(self):
        exc = EmailAttachmentBlockedByProvider("blocked")
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentBlockedExtension)

    def test_too_large_for_provider_maps_to_too_large(self):
        exc = EmailAttachmentTooLargeForProvider("too big")
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentTooLarge)

    def test_send_failed_maps_to_send_failed_with_detail(self):
        exc = EmailAttachmentSendFailed(
            "failed",
            {"failed_attachments": [{"draft_attachment_id": "x", "filename": "a.pdf", "reason": "boom"}]},
        )
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentSendFailed)
        assert result.detail.get("failed_attachments") == [
            {"draft_attachment_id": "x", "filename": "a.pdf", "reason": "boom"},
        ]

    def test_download_failed_with_forbidden_reason_maps_to_provider_forbidden(self):
        # Reason ``forbidden`` -> 502 (provider_forbidden).
        exc = EmailAttachmentDownloadFailed("oops", {"reason": "forbidden"})
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentProviderForbidden)

    def test_download_failed_with_unavailable_reason_maps_to_provider_unavailable(self):
        # Reason ``unavailable`` -> 503 (provider_unavailable).
        exc = EmailAttachmentDownloadFailed("oops", {"reason": "unavailable"})
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentProviderUnavailable)

    def test_download_failed_with_no_reason_maps_to_provider_unavailable(self):
        # Default branch when ``detail.reason`` is missing.
        exc = EmailAttachmentDownloadFailed("oops", {})
        result = services_helpers.translate_core_error(exc)
        assert isinstance(result, AttachmentProviderUnavailable)
