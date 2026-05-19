"""
Unit tests for the new attachment passthroughs added to ``EmailManager``:

- ``list_message_attachments`` — delegates to client.
- ``fetch_attachment_binary`` — delegates to client, wraps unexpected errors.
- ``send_draft_with_attachments`` — delegates to client, wraps unexpected errors.

The manager pattern is uniform across operations: it locates the client
by ``account_label``, lets ``CoreError`` re-raise unchanged, and wraps
anything else as ``EmailExternalAPIError``. The tests below mirror the
shape of ``test_email_manager_extended.py`` for parity.
"""

from __future__ import annotations

import pytest

from core.email import (
    AttachmentBinary,
    AttachmentMetadata,
    AttachmentUploadResult,
    DraftAttachmentInput,
    EmailManager,
)
from core.email.errors import (
    EmailAccountNotFoundError,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailAttachmentSendFailed,
    EmailExternalAPIError,
)
from tests.shared.email_fakes import FakeEmailClient, build_metadata


_LABEL = "mb__acc"


def _build_manager(**fake_kwargs) -> tuple[EmailManager, FakeEmailClient]:
    manager = EmailManager()
    client = FakeEmailClient(_LABEL, **fake_kwargs)
    manager.add_client(client)
    return manager, client


def _attachment_meta() -> AttachmentMetadata:
    return AttachmentMetadata(
        provider_message_id="msg-1",
        part_id="0.1",
        provider_attachment_id=None,
        filename="report.pdf",
        mime_type="application/pdf",
        size=4,
        content_id=None,
        is_inline=False,
        position=0,
    )


# ── list_message_attachments ───────────────────────────────────────


class TestListMessageAttachments:

    def test_delegates_to_client_and_returns_pair(self):
        meta = _attachment_meta()
        manager, client = _build_manager(
            list_message_attachments_return=([meta], {"cid1": "data:image/png;base64,AAA"}),
        )
        downloadable, cid_map = manager.list_message_attachments(_LABEL, "msg-1")
        assert downloadable == [meta]
        assert cid_map == {"cid1": "data:image/png;base64,AAA"}
        assert client.list_message_attachments_calls == ["msg-1"]

    def test_unknown_account_raises_account_not_found(self):
        manager, _ = _build_manager()
        with pytest.raises(EmailAccountNotFoundError):
            manager.list_message_attachments("not-a-label", "msg-1")

    def test_core_error_propagates_unchanged(self):
        manager, _ = _build_manager(
            list_message_attachments_exc=EmailAttachmentNotFound("gone"),
        )
        with pytest.raises(EmailAttachmentNotFound):
            manager.list_message_attachments(_LABEL, "msg-1")

    def test_unexpected_exception_wrapped_as_external_api_error(self):
        manager, _ = _build_manager(
            list_message_attachments_exc=RuntimeError("boom"),
        )
        with pytest.raises(EmailExternalAPIError, match="list_message_attachments"):
            manager.list_message_attachments(_LABEL, "msg-1")


# ── fetch_attachment_binary ────────────────────────────────────────


class TestFetchAttachmentBinary:

    def test_delegates_to_client_and_returns_binary(self):
        binary = AttachmentBinary(
            mime_type="application/pdf", filename="r.pdf", data=b"X", size=1,
        )
        manager, client = _build_manager(fetch_attachment_binary_return=binary)
        meta = _attachment_meta()
        result = manager.fetch_attachment_binary(_LABEL, "msg-1", meta)
        assert result is binary
        assert client.fetch_attachment_binary_calls == [("msg-1", meta)]

    def test_unknown_account_raises_account_not_found(self):
        manager, _ = _build_manager()
        with pytest.raises(EmailAccountNotFoundError):
            manager.fetch_attachment_binary("nope", "msg-1", _attachment_meta())

    def test_attachment_not_found_propagates_unchanged(self):
        manager, _ = _build_manager(
            fetch_attachment_binary_exc=EmailAttachmentNotFound("gone"),
        )
        with pytest.raises(EmailAttachmentNotFound):
            manager.fetch_attachment_binary(_LABEL, "msg-1", _attachment_meta())

    def test_download_failed_propagates_unchanged(self):
        manager, _ = _build_manager(
            fetch_attachment_binary_exc=EmailAttachmentDownloadFailed(
                "5xx", {"reason": "unavailable"},
            ),
        )
        with pytest.raises(EmailAttachmentDownloadFailed):
            manager.fetch_attachment_binary(_LABEL, "msg-1", _attachment_meta())

    def test_unexpected_exception_wrapped(self):
        manager, _ = _build_manager(
            fetch_attachment_binary_exc=RuntimeError("boom"),
        )
        with pytest.raises(EmailExternalAPIError, match="fetch_attachment_binary"):
            manager.fetch_attachment_binary(_LABEL, "msg-1", _attachment_meta())


# ── send_draft_with_attachments ────────────────────────────────────


class TestSendDraftWithAttachments:

    def _input(self, **overrides) -> DraftAttachmentInput:
        defaults = {
            "draft_attachment_id": "att-1",
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "data": b"X",
            "size": 1,
            "position": 0,
        }
        defaults.update(overrides)
        return DraftAttachmentInput(**defaults)

    def test_delegates_and_returns_metadata_plus_uploads(self):
        sent = build_metadata(
            provider_message_id="sent-1", subject="hi", box="SENT", is_read=True,
        )
        upload = AttachmentUploadResult(
            draft_attachment_id="att-1", provider_attachment_id="prov-1",
        )
        manager, client = _build_manager(
            send_draft_with_attachments_return=(sent, [upload]),
        )
        meta, uploads = manager.send_draft_with_attachments(
            _LABEL, "draft-1",
            ["to@x"], [], [], "subject", "body",
            [self._input()],
        )
        assert meta is sent
        assert uploads == [upload]
        assert len(client.send_draft_with_attachments_calls) == 1
        call = client.send_draft_with_attachments_calls[0]
        assert call[0] == "draft-1"
        assert call[1] == ["to@x"]
        assert call[4] == "subject"
        assert call[5] == "body"

    def test_unknown_account_raises_account_not_found(self):
        manager, _ = _build_manager()
        with pytest.raises(EmailAccountNotFoundError):
            manager.send_draft_with_attachments(
                "nope", "draft-1", [], [], [], "s", "b", [],
            )

    def test_send_failed_propagates_unchanged(self):
        manager, _ = _build_manager(
            send_draft_with_attachments_exc=EmailAttachmentSendFailed(
                "failed", {"failed_attachments": [{"draft_attachment_id": "x"}]},
            ),
        )
        with pytest.raises(EmailAttachmentSendFailed):
            manager.send_draft_with_attachments(
                _LABEL, "draft-1", [], [], [], "s", "b", [],
            )

    def test_unexpected_exception_wrapped(self):
        manager, _ = _build_manager(
            send_draft_with_attachments_exc=RuntimeError("boom"),
        )
        with pytest.raises(EmailExternalAPIError, match="send_draft_with_attachments"):
            manager.send_draft_with_attachments(
                _LABEL, "draft-1", [], [], [], "s", "b", [],
            )
