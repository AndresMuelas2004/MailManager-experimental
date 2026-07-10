"""Tests espejo de ``outlook_client.envio`` (send_email y envio de borradores con adjuntos)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.email.email_client import EmailMetadata
from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.outlook_client import OutlookClient, _extract_attachment_id_from_location

from ._helpers import _make_authenticated_client


# ── send_email ───────────────────────────────────────────────────


class TestSendEmail:
    def _draft_response(self, draft_id="draft1"):
        return {
            "id": draft_id,
            "conversationId": "conv1",
            "from": {"emailAddress": {"address": "me@outlook.com", "name": "Me"}},
            "subject": "Subject",
            "receivedDateTime": "2024-06-01T12:00:00Z",
            "isRead": True,
        }

    def test_draft_then_send_two_calls(self):
        """send_email creates a draft then sends it (2 graph calls)."""
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=[
            self._draft_response(), {},
        ]) as mock_graph:
            result = client.send_email("Subject", "<p>Body</p>", ["a@b.com"])

        assert mock_graph.call_count == 2
        # First call: create draft
        first_args, first_kwargs = mock_graph.call_args_list[0]
        assert first_args[0] == "POST"
        assert "/me/messages" in first_args[1]
        assert "/send" not in first_args[1]
        draft_body = first_kwargs.get("body", first_args[2] if len(first_args) > 2 else None)
        assert draft_body["subject"] == "Subject"
        # HTML send: Graph stores the body as contentType=HTML and the content
        # is the composed HTML verbatim.
        assert draft_body["body"]["contentType"] == "HTML"
        assert draft_body["body"]["content"] == "<p>Body</p>"
        # Second call: send
        second_args, _ = mock_graph.call_args_list[1]
        assert second_args[0] == "POST"
        assert "/me/messages/draft1/send" in second_args[1]

    def test_returns_email_metadata(self):
        """send_email returns EmailMetadata parsed from the draft response."""
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=[
            self._draft_response(), {},
        ]):
            result = client.send_email("Subject", "Body", ["a@b.com"])

        assert result.provider_message_id == "draft1"
        assert result.thread_id == "conv1"
        assert result.from_email == "me@outlook.com"
        assert result.subject == "Subject"
        assert result.box == "SENT"

    def test_multiple_recipients_payload(self):
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=[
            self._draft_response(), {},
        ]) as mock_graph:
            client.send_email("S", "B", ["a@b.com", "c@d.com", "e@f.com"])
            first_args, first_kwargs = mock_graph.call_args_list[0]
            payload = first_kwargs.get("body", first_args[2] if len(first_args) > 2 else None)
            addrs = [r["emailAddress"]["address"] for r in payload["toRecipients"]]
            assert addrs == ["a@b.com", "c@d.com", "e@f.com"]

    def test_supplements_sender_from_profile_when_draft_has_no_from(self):
        """Draft response without 'from' — sender fields supplemented after send."""
        client = _make_authenticated_client()
        draft_no_from = {
            "id": "draft1",
            "conversationId": "conv1",
            "subject": "Subject",
            "receivedDateTime": "2024-06-01T12:00:00Z",
            "isRead": True,
        }
        profile_response = {
            "displayName": "Test User",
            "mail": "me@outlook.com",
        }

        def mock_graph(method, url, body=None):
            if method == "POST" and "/me/messages" in url and "/send" not in url:
                return draft_no_from
            if method == "POST" and "/send" in url:
                return {}
            if method == "GET" and "/me?" in url and "mailFolders" not in url:
                return profile_response
            raise AssertionError(f"Unexpected call: {method} {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.send_email("Subject", "Body", ["a@b.com"])
        assert result.from_email == "me@outlook.com"
        assert result.from_name == "Test User"

    def test_supplements_sender_from_sentitems_fallback(self):
        """When /me and JWT fail, reads sender from a recently sent message."""
        client = _make_authenticated_client()
        draft_no_from = {
            "id": "draft1",
            "conversationId": "conv1",
            "subject": "Subject",
            "receivedDateTime": "2024-06-01T12:00:00Z",
            "isRead": True,
        }
        sentitems_response = {
            "value": [{
                "from": {"emailAddress": {"address": "me@outlook.com", "name": "Sent User"}},
            }],
        }

        call_index = 0

        def mock_graph(method, url, body=None):
            nonlocal call_index
            call_index += 1
            if method == "POST" and "/me/messages" in url and "/send" not in url:
                return draft_no_from
            if method == "POST" and "/send" in url:
                return {}
            if method == "GET" and "/me?" in url and "mailFolders" not in url:
                raise EmailExternalAPIError("no User.Read scope")
            if method == "GET" and "sentitems/messages" in url:
                return sentitems_response
            raise AssertionError(f"Unexpected call: {method} {url}")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.send_email("Subject", "Body", ["a@b.com"])
        assert result.from_email == "me@outlook.com"
        assert result.from_name == "Sent User"

    def test_uses_draft_from_when_available(self):
        """Draft with 'from' — no profile call made (only 2 graph calls)."""
        client = _make_authenticated_client()
        with patch.object(client, "_graph_request", side_effect=[
            self._draft_response(), {},
        ]) as mock_graph:
            result = client.send_email("Subject", "Body", ["a@b.com"])
        assert mock_graph.call_count == 2
        assert result.from_email == "me@outlook.com"
        assert result.from_name == "Me"


# ── send_draft_with_attachments ────────────────────────────────────


class TestExtractAttachmentIdFromLocation:
    """``_extract_attachment_id_from_location`` parses the ImmutableId out of a
    ``createUploadSession`` terminal Location header. It is the only thing that
    recovers the upload-session attachment id, and the string-parse is fragile,
    so it deserves direct coverage."""

    def test_extracts_id_from_realistic_location(self):
        location = (
            "https://outlook.office.com/api/v2.0/Users('u')/Messages('m')/"
            "Attachments('AAMkAGI2THVSAAA=')"
        )
        assert _extract_attachment_id_from_location(location) == "AAMkAGI2THVSAAA="

    def test_returns_none_when_attachments_segment_missing(self):
        location = "https://outlook.office.com/api/v2.0/Users('u')/Messages('m')"
        assert _extract_attachment_id_from_location(location) is None

    def test_returns_none_for_empty_location(self):
        assert _extract_attachment_id_from_location("") is None


class TestOutlookSendDraftWithAttachments:
    """Cover the non-atomic Outlook send-with-attachments path (D-07, D-18, D-27)."""

    def _attachment_input(
        self,
        *,
        draft_attachment_id: str = "local-1",
        provider_attachment_id: str | None = None,
        size: int = 100,
        data: bytes = b"PDF",
        position: int = 0,
    ):
        from core.email.email_client import DraftAttachmentInput
        return DraftAttachmentInput(
            draft_attachment_id=draft_attachment_id,
            filename="doc.pdf",
            mime_type="application/pdf",
            data=data,
            size=size,
            position=position,
            content_id=None,
            is_inline=False,
            provider_attachment_id=provider_attachment_id,
        )

    def test_unauthenticated_raises(self):
        client = OutlookClient(account_label="mb__acct")
        with pytest.raises(EmailNotAuthenticatedError):
            client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "S", "B",
                [self._attachment_input()],
            )

    def _stub_metadata(self, monkeypatch, authenticated_client):
        """Replace `_build_outlook_sent_metadata` with a synthetic stub
        carrying the bare-minimum fields the assertions need."""
        from core.email.email_client import EmailMetadata
        from datetime import datetime, timezone

        def _fake(
            provider_draft_id: str,
            to_recipients: list[str] | None = None,
        ) -> EmailMetadata:
            return EmailMetadata(
                provider_message_id=provider_draft_id,
                thread_id=None,
                from_email="me@outlook.test",
                from_name="Me",
                subject="Subject",
                received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                is_read=True,
                box="SENT",
            )

        monkeypatch.setattr(
            authenticated_client, "_build_outlook_sent_metadata", _fake,
        )

    def test_simple_upload_then_send_returns_metadata_and_uploads(
        self, authenticated_client, monkeypatch,
    ):
        # Stub the simple upload to return a synthetic provider attachment id.
        monkeypatch.setattr(
            authenticated_client, "_upload_attachment_simple",
            lambda did, att: "graph-att-1",
        )
        # The send POST goes through _graph_request — return an inert dict.
        monkeypatch.setattr(
            authenticated_client, "_graph_request",
            lambda method, url, body=None, extra_headers=None: {},
        )
        self._stub_metadata(monkeypatch, authenticated_client)

        metadata, uploads = authenticated_client.send_draft_with_attachments(
            "draft-1", ["to@x"], [], [], "Subject", "Body",
            [self._attachment_input()],
        )
        assert metadata.box == "SENT"
        assert len(uploads) == 1
        assert uploads[0].provider_attachment_id == "graph-att-1"
        assert uploads[0].draft_attachment_id == "local-1"

    def test_large_attachment_routes_through_upload_session_not_simple(
        self, authenticated_client, monkeypatch,
    ):
        # A >=3 MB per-attachment payload must take the createUploadSession +
        # chunked-PUT path (_upload_attachment_via_session), NOT the simple
        # POST. ``pick_outlook_attachment_strategy`` decides on ``att.size``,
        # so the strategy can be exercised without 3 MB of real bytes. Without
        # this test the entire chunked path is unexercised at the unit level.
        from core.email.helpers import _OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES

        via_session_calls: list[str] = []
        monkeypatch.setattr(
            authenticated_client, "_upload_attachment_via_session",
            lambda did, att: via_session_calls.append(att.draft_attachment_id) or "graph-session-att",
        )

        def _simple_must_not_run(did, att):
            raise AssertionError("simple upload must not run for a >=3 MB attachment")

        monkeypatch.setattr(
            authenticated_client, "_upload_attachment_simple", _simple_must_not_run,
        )
        monkeypatch.setattr(
            authenticated_client, "_graph_request",
            lambda method, url, body=None, extra_headers=None: {},
        )
        self._stub_metadata(monkeypatch, authenticated_client)

        big = self._attachment_input(
            size=_OUTLOOK_UPLOAD_SESSION_THRESHOLD_BYTES,  # exactly at the inclusive cutoff
            data=b"x" * 16,  # size (not byte length) drives the strategy
        )
        _, uploads = authenticated_client.send_draft_with_attachments(
            "draft-1", ["to@x"], [], [], "Subject", "Body", [big],
        )
        assert via_session_calls == ["local-1"]
        assert uploads[0].provider_attachment_id == "graph-session-att"

    def test_attachment_with_provider_id_skips_reupload(
        self, authenticated_client, monkeypatch,
    ):
        upload_calls: list[str] = []
        monkeypatch.setattr(
            authenticated_client, "_upload_attachment_simple",
            lambda did, att: upload_calls.append(att.draft_attachment_id) or "x",
        )
        monkeypatch.setattr(
            authenticated_client, "_graph_request",
            lambda method, url, body=None, extra_headers=None: {},
        )
        self._stub_metadata(monkeypatch, authenticated_client)

        already_uploaded = self._attachment_input(
            draft_attachment_id="resumed-1",
            provider_attachment_id="graph-att-existing",
        )
        _, uploads = authenticated_client.send_draft_with_attachments(
            "draft-1", ["to@x"], [], [], "Subject", "Body",
            [already_uploaded],
        )
        # No re-upload, but the existing id surfaces back in the result list.
        assert upload_calls == []
        assert len(uploads) == 1
        assert uploads[0].provider_attachment_id == "graph-att-existing"

    def test_mid_flight_failure_raises_email_attachment_send_failed_with_succeeded(
        self, authenticated_client, monkeypatch,
    ):
        from core.email.errors import EmailAttachmentSendFailed

        # First attachment uploads ok; second raises EmailExternalAPIError.
        upload_iter = iter([
            ("ok-id-1", None),
            (None, EmailExternalAPIError("provider 500")),
        ])

        def _upload(did, att):
            value, exc = next(upload_iter)
            if exc is not None:
                raise exc
            return value

        monkeypatch.setattr(authenticated_client, "_upload_attachment_simple", _upload)

        with pytest.raises(EmailAttachmentSendFailed) as excinfo:
            authenticated_client.send_draft_with_attachments(
                "draft-1", ["to@x"], [], [], "Subject", "Body",
                [
                    self._attachment_input(draft_attachment_id="ok-1"),
                    self._attachment_input(draft_attachment_id="fail-1"),
                ],
            )
        detail = excinfo.value.detail or {}
        # D-27 contract: succeeded list carries the partial state for retry resume.
        assert any(
            entry.get("draft_attachment_id") == "ok-1"
            and entry.get("provider_attachment_id") == "ok-id-1"
            for entry in detail.get("succeeded", [])
        )
        assert any(
            entry.get("draft_attachment_id") == "fail-1"
            for entry in detail.get("failed_attachments", [])
        )


# ── send_draft_with_attachments — reply signature symmetry ─────────


class TestOutlookSendDraftReplySymmetry:
    """The Outlook send-path accepts ``in_reply_to`` / ``references`` /
    ``thread_id`` kwargs for signature symmetry with Gmail but **does NOT
    use them on the wire** — Outlook stitches the thread server-side via
    createReply / createReplyAll / createForward at draft creation, so
    repeating the data on send would be redundant and Graph provides no
    header-injection path for ``POST /messages/{id}/send``.

    This contract is load-bearing: if a refactor silently switches Outlook
    to inject these headers, integration tests that assume "thread already
    fixed" would still pass but the Outlook surface would diverge from
    Gmail and double-count thread metadata.
    """

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
            provider_attachment_id="already-uploaded-id",
        )

    def test_reply_kwargs_accepted_without_error(
        self, authenticated_client, monkeypatch,
    ):
        # No re-upload (provider_attachment_id already set) and send
        # succeeds — the kwargs are silently swallowed.
        captured_sends: list[tuple[str, dict | None]] = []

        def _graph_request(method, url, body=None, extra_headers=None):
            captured_sends.append((url, body))
            return {"id": "draft-1"}

        monkeypatch.setattr(
            authenticated_client, "_graph_request", _graph_request,
        )
        monkeypatch.setattr(
            authenticated_client, "_build_outlook_sent_metadata",
            lambda pdid, to_recipients=None: __import__("tests.shared.email_fakes", fromlist=["build_metadata"]).build_metadata(
                provider_message_id=pdid, subject="Re: Hi", box="SENT", is_read=True,
            ),
        )
        sent_meta, uploads = authenticated_client.send_draft_with_attachments(
            "draft-1", ["to@x"], [], [], "Re: Hi", "body",
            [self._attachment_input()],
            in_reply_to="<orig@x>",
            references="<orig@x>",
            thread_id="conv-1",
        )
        # The send URL is hit (signature accepted, no upload because the
        # only attachment already has a provider id).
        assert any("/send" in url for url, _b in captured_sends)
        assert len(uploads) == 1
        # And the kwargs do NOT leak into the request body (Graph has
        # no header-injection path here).
        for _url, body in captured_sends:
            if body:
                assert "In-Reply-To" not in str(body)
                assert "References" not in str(body)
                assert "threadId" not in (body or {})
                assert "conversationId" not in (body or {})
