"""Tests espejo de ``gmail_client.envio`` (send_email y envio de borradores con adjuntos)."""

from __future__ import annotations

import base64
from datetime import datetime
from email import message_from_bytes, policy
from unittest.mock import MagicMock, patch

import pytest

from core.email.email_client import EmailMetadata
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.gmail_client import GmailClient


# ── send_email ───────────────────────────────────────────────────


class TestSendEmail:
    def _setup_send_mock(self, client: GmailClient, send_response: dict | None = None):
        """Set up mock service for send_email with proper MagicMock chaining."""
        mock_service = MagicMock()
        client.service = mock_service
        if send_response is None:
            send_response = {"id": "msg1", "threadId": "th1", "labelIds": ["SENT"]}
        # Chain: service.users().messages().send(userId=..., body=...).execute()
        mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = send_response
        return mock_service

    def test_constructs_mime_and_calls_api(self, client: GmailClient):
        """Verify the HTML multipart/alternative MIME structure and API call."""
        mock_service = self._setup_send_mock(client)
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            client.send_email("Test Subject", "<p>Test Body</p>", ["a@b.com"])

        send_fn = mock_service.users.return_value.messages.return_value.send
        send_fn.assert_called_once()
        call_kwargs = send_fn.call_args
        body = call_kwargs[1]["body"]
        raw_bytes = base64.urlsafe_b64decode(body["raw"])
        msg = message_from_bytes(raw_bytes, policy=policy.default)
        assert msg["subject"] == "Test Subject"
        assert "a@b.com" in (msg["to"] or "")
        # HTML send is a multipart/alternative: derived text/plain leg + the
        # text/html leg carrying the composed HTML.
        assert msg.get_content_type() == "multipart/alternative"
        subtypes = [p.get_content_type() for p in msg.iter_parts()]
        assert "text/plain" in subtypes
        assert "text/html" in subtypes
        html_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
        assert "<p>Test Body</p>" in html_part.get_content()

    def test_returns_metadata_from_batch_fetch(self, client: GmailClient):
        """send_email returns EmailMetadata fetched via fetch_messages_metadata."""
        from core.email.email_client import EmailMetadata
        self._setup_send_mock(client, {"id": "msg123", "threadId": "th456", "labelIds": ["SENT"]})
        expected = EmailMetadata(
            provider_message_id="msg123", thread_id="th456",
            from_email="me@gmail.com", from_name="Me",
            subject="Hello", received_at=datetime(2024, 1, 1),
            is_read=True, box="SENT",
        )
        with patch.object(client, "fetch_messages_metadata", return_value=[expected]) as mock_fetch:
            result = client.send_email("Hello", "Body", ["a@b.com"])

        mock_fetch.assert_called_once_with(["msg123"])
        assert result is expected

    def test_returns_fallback_metadata_when_fetch_empty(self, client: GmailClient):
        """When fetch_messages_metadata returns empty, fallback metadata is built."""
        self._setup_send_mock(client, {"id": "msg123", "threadId": "th456"})
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            result = client.send_email("Subject", "Body", ["a@b.com"])

        assert result.provider_message_id == "msg123"
        assert result.thread_id == "th456"
        assert result.box == "SENT"
        assert result.is_read is True
        assert result.subject == "Subject"

    def test_http_error_raises_external_api(self, client: GmailClient):
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.reason = "Forbidden"
        http_err = HttpError(resp=mock_resp, content=b"forbidden")

        mock_service = self._setup_send_mock(client)
        mock_service.users.return_value.messages.return_value.send.return_value.execute.side_effect = http_err

        with pytest.raises(EmailExternalAPIError, match="Gmail failed to send email"):
            client.send_email("S", "B", ["a@b.com"])

    def test_unexpected_error_raises_external_api(self, client: GmailClient):
        mock_service = self._setup_send_mock(client)
        mock_service.users.return_value.messages.return_value.send.return_value.execute.side_effect = RuntimeError("boom")

        with pytest.raises(EmailExternalAPIError, match="Gmail unexpected send email error"):
            client.send_email("S", "B", ["a@b.com"])

    def test_supplements_subject_when_batch_fetch_returns_empty(self, client: GmailClient):
        """Batch fetch returns EmailMetadata with empty subject — supplemented from parameter."""
        from core.email.email_client import EmailMetadata
        self._setup_send_mock(client, {"id": "msg1", "threadId": "th1"})
        fetched = EmailMetadata(
            provider_message_id="msg1", thread_id="th1",
            from_email="me@gmail.com", from_name="Me",
            subject="", received_at=datetime(2024, 1, 1),
            is_read=True, box="SENT",
        )
        with patch.object(client, "fetch_messages_metadata", return_value=[fetched]):
            result = client.send_email("Real Subject", "Body", ["a@b.com"])
        assert result.subject == "Real Subject"

    def test_supplements_from_email_from_profile(self, client: GmailClient):
        """Batch fetch returns empty from_email — supplemented from getProfile."""
        from core.email.email_client import EmailMetadata
        self._setup_send_mock(client, {"id": "msg1", "threadId": "th1"})
        fetched = EmailMetadata(
            provider_message_id="msg1", thread_id="th1",
            from_email="", from_name="",
            subject="S", received_at=datetime(2024, 1, 1),
            is_read=True, box="SENT",
        )
        with patch.object(client, "fetch_messages_metadata", return_value=[fetched]), \
             patch.object(client, "_fetch_sender_email", return_value="me@gmail.com"):
            result = client.send_email("S", "Body", ["a@b.com"])
        assert result.from_email == "me@gmail.com"

    def test_supplements_from_name_with_from_email(self, client: GmailClient):
        """Batch fetch returns from_email but empty from_name — from_name set to from_email."""
        from core.email.email_client import EmailMetadata
        self._setup_send_mock(client, {"id": "msg1", "threadId": "th1"})
        fetched = EmailMetadata(
            provider_message_id="msg1", thread_id="th1",
            from_email="me@gmail.com", from_name="",
            subject="S", received_at=datetime(2024, 1, 1),
            is_read=True, box="SENT",
        )
        with patch.object(client, "fetch_messages_metadata", return_value=[fetched]):
            result = client.send_email("S", "Body", ["a@b.com"])
        assert result.from_name == "me@gmail.com"

    def test_fallback_metadata_uses_profile_email(self, client: GmailClient):
        """Fallback path (batch fetch empty) uses profile email for from fields."""
        self._setup_send_mock(client, {"id": "msg1", "threadId": "th1"})
        with patch.object(client, "fetch_messages_metadata", return_value=[]), \
             patch.object(client, "_fetch_sender_email", return_value="me@gmail.com"):
            result = client.send_email("Subject", "Body", ["a@b.com"])
        assert result.from_email == "me@gmail.com"
        assert result.from_name == "me@gmail.com"
        assert result.subject == "Subject"


# ── send_draft_with_attachments ────────────────────────────────────


class TestSendDraftWithAttachments:
    """Cover the atomic Gmail send-with-attachments path (D-07, D-18, D-27)."""

    def _setup_drafts_send_mock(self, client: GmailClient, response: dict | None = None):
        mock_service = MagicMock()
        client.service = mock_service
        if response is None:
            response = {"id": "sent-msg-1", "threadId": "th-1", "labelIds": ["SENT"]}
        mock_service.users.return_value.drafts.return_value.send.return_value.execute.return_value = response
        return mock_service

    def _attachment_input(self, filename: str = "doc.pdf", data: bytes = b"PDF"):
        from core.email.email_client import DraftAttachmentInput
        return DraftAttachmentInput(
            draft_attachment_id="local-1",
            filename=filename,
            mime_type="application/pdf",
            data=data,
            size=len(data),
            position=0,
            content_id=None,
            is_inline=False,
        )

    def test_simple_strategy_calls_drafts_send_atomically(self, client: GmailClient):
        mock_service = self._setup_drafts_send_mock(client)
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            metadata, uploads = client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "Subject", "Body",
                [self._attachment_input()],
            )
        send_fn = mock_service.users.return_value.drafts.return_value.send
        send_fn.assert_called_once()
        # Atomic Gmail send returns no per-attachment intermediate state.
        assert uploads == []
        # The body wraps the existing draft id so Gmail replaces in-place.
        body = send_fn.call_args[1]["body"]
        assert body["id"] == "draft-1"
        assert "raw" in body["message"]
        assert metadata.box == "SENT"

    def test_blocked_attachment_400_raises_blocked_by_provider(self, client: GmailClient):
        from googleapiclient.errors import HttpError
        from core.email.errors import EmailAttachmentBlockedByProvider

        mock_resp = MagicMock()
        mock_resp.status = 400
        mock_resp.reason = "Attachment is invalid: not allowed"
        http_err = HttpError(resp=mock_resp, content=b"blocked")

        mock_service = self._setup_drafts_send_mock(client)
        mock_service.users.return_value.drafts.return_value.send.return_value.execute.side_effect = http_err

        with pytest.raises(EmailAttachmentBlockedByProvider):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "S", "B",
                [self._attachment_input()],
            )

    def test_unauthenticated_raises(self, client: GmailClient):
        client.service = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "S", "B",
                [self._attachment_input()],
            )


# ── _build_send_message_payload (static helper) ────────────────────


class TestBuildSendMessagePayload:
    """Covers the message sub-payload used by drafts.send simple + resumable."""

    def test_without_thread_id_returns_just_raw(self):
        out = GmailClient._build_send_message_payload("RAW", None)
        assert out == {"raw": "RAW"}

    def test_with_thread_id_includes_field(self):
        out = GmailClient._build_send_message_payload("RAW", "thread-1")
        assert out == {"raw": "RAW", "threadId": "thread-1"}

    def test_empty_thread_id_excluded(self):
        # Empty string is falsy → no threadId field on the wire.
        out = GmailClient._build_send_message_payload("RAW", "")
        assert "threadId" not in out


# ── send_draft_with_attachments — reply metadata propagation ───────


class TestGmailSendDraftReplyHeaders:
    """Covers the ``in_reply_to`` / ``references`` / ``thread_id`` kwargs."""

    def _setup_drafts_send_mock(self, client: GmailClient, response: dict | None = None):
        mock_service = MagicMock()
        client.service = mock_service
        if response is None:
            response = {"id": "sent-msg-1", "threadId": "thread-1", "labelIds": ["SENT"]}
        mock_service.users.return_value.drafts.return_value.send.return_value.execute.return_value = response
        return mock_service

    def _attachment_input(self):
        from core.email.email_client import DraftAttachmentInput
        return DraftAttachmentInput(
            draft_attachment_id="local-1",
            filename="doc.pdf",
            mime_type="application/pdf",
            data=b"PDF",
            size=3,
            position=0,
            content_id=None,
            is_inline=False,
        )

    def test_thread_id_propagated_to_send_payload(self, client: GmailClient):
        mock_service = self._setup_drafts_send_mock(client)
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "Re: Hi", "body",
                [self._attachment_input()],
                thread_id="thread-1",
            )
        body = mock_service.users.return_value.drafts.return_value.send.call_args[1]["body"]
        # threadId rides in ``message`` so Gmail stitches the send into the thread.
        assert body["message"].get("threadId") == "thread-1"

    def test_in_reply_to_and_references_injected_into_mime(self, client: GmailClient):
        mock_service = self._setup_drafts_send_mock(client)
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "Re: Hi", "body",
                [self._attachment_input()],
                in_reply_to="<orig@x>", references="<orig@x>",
            )
        raw_b64 = (
            mock_service.users.return_value.drafts.return_value.send.call_args[1]
            ["body"]["message"]["raw"]
        )
        padding = "=" * (4 - len(raw_b64) % 4)
        mime_text = base64.urlsafe_b64decode(raw_b64 + padding).decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in mime_text
        assert "References: <orig@x>" in mime_text

    def test_no_reply_kwargs_omits_threadId_and_headers(self, client: GmailClient):
        # Backward compat: regular send-draft path (no reply context) does
        # not introduce threadId or reply headers.
        mock_service = self._setup_drafts_send_mock(client)
        with patch.object(client, "fetch_messages_metadata", return_value=[]):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "Hi", "body",
                [self._attachment_input()],
            )
        body = mock_service.users.return_value.drafts.return_value.send.call_args[1]["body"]
        assert "threadId" not in body["message"]
        raw_b64 = body["message"]["raw"]
        padding = "=" * (4 - len(raw_b64) % 4)
        mime_text = base64.urlsafe_b64decode(raw_b64 + padding).decode("utf-8", errors="replace")
        assert "In-Reply-To" not in mime_text
        assert "References" not in mime_text
