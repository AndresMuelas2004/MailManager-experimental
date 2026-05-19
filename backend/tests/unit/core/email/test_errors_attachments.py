"""
Unit tests for the attachment-related additions to the core email
error hierarchy. Verifies inheritance, codes, default messages, and
that ``detail`` carries through for the contracts the API layer relies
on (``failed_attachments`` for send-failed, ``reason`` for download-
failed).
"""

from __future__ import annotations

import pytest

from core.email.errors import (
    CoreError,
    EmailAttachmentBlockedByProvider,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailAttachmentTooLargeForProvider,
    EmailError,
)


_ATTACHMENT_CLASSES = [
    EmailAttachmentNotFound,
    EmailAttachmentDownloadFailed,
    EmailAttachmentBlockedByProvider,
    EmailAttachmentTooLargeForProvider,
    EmailAttachmentSendFailed,
]


@pytest.mark.parametrize("cls", _ATTACHMENT_CLASSES)
def test_attachment_errors_inherit_email_error(cls):
    assert issubclass(cls, EmailError)
    assert issubclass(cls, CoreError)


@pytest.mark.parametrize("cls", _ATTACHMENT_CLASSES)
def test_attachment_errors_have_unique_code(cls):
    assert cls.code, f"{cls.__name__} has no code"


def test_attachment_codes_are_distinct():
    codes = [cls.code for cls in _ATTACHMENT_CLASSES]
    assert len(codes) == len(set(codes)), f"Duplicate codes: {codes}"


@pytest.mark.parametrize("cls", _ATTACHMENT_CLASSES)
def test_attachment_errors_have_default_message(cls):
    assert cls.default_message


def test_download_failed_carries_reason_in_detail():
    # The reason is the contract used by `translate_core_error` to split
    # 502 (forbidden) vs 503 (unavailable). Tests covering the split live
    # in `test_services_helpers_attachments.py`; here we verify that the
    # detail dict is preserved by the base class.
    exc = EmailAttachmentDownloadFailed("oops", {"reason": "forbidden"})
    assert exc.detail == {"reason": "forbidden"}


def test_send_failed_carries_failed_attachments_in_detail():
    detail = {
        "failed_attachments": [
            {"draft_attachment_id": "a1", "filename": "a.pdf", "reason": "boom"},
            {"draft_attachment_id": "a2", "filename": "b.pdf", "reason": "boom"},
        ]
    }
    exc = EmailAttachmentSendFailed("send broke", detail)
    assert exc.detail == detail
    assert len(exc.detail["failed_attachments"]) == 2


def test_default_construction_falls_back_to_default_message():
    exc = EmailAttachmentNotFound()
    assert exc.message == EmailAttachmentNotFound.default_message
    assert exc.detail == {}


def test_attachment_errors_are_catchable_as_core_error():
    with pytest.raises(CoreError):
        raise EmailAttachmentNotFound("x")
    with pytest.raises(CoreError):
        raise EmailAttachmentDownloadFailed("x", {"reason": "forbidden"})
