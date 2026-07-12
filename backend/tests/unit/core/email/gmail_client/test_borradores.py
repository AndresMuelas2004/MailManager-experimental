"""Tests espejo de ``gmail_client.borradores`` (CRUD de borradores y Reply/Forward)."""

from __future__ import annotations

import base64
from email import message_from_bytes, policy
from unittest.mock import MagicMock, patch

import pytest

from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.gmail_client import GmailClient, _DRAFTS_MAX_TOTAL


# ── fetch_drafts ────────────────────────────────────────────────────


def _encode_body(text: str) -> str:
    raw = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def _gmail_draft_response(draft_id: str, subject: str = "Hello") -> dict:
    """Build a fake Gmail drafts.get response with format=full."""
    return {
        "id": draft_id,
        "message": {
            "id": f"msg-{draft_id}",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "To", "value": "Alice <a@b.com>, c@d.com"},
                    {"name": "Cc", "value": "e@f.com"},
                    {"name": "Bcc", "value": ""},
                ],
                "mimeType": "text/html",
                "body": {"data": _encode_body(f"<p>body-{draft_id}</p>")},
            },
        },
    }


class TestFetchDrafts:
    def test_max_total_cap_is_500(self):
        # The per-account draft fetch cap was raised 100 -> 500. This constant is
        # the single source of truth the cap tests below assert against, so pin
        # its value explicitly (a silent revert would otherwise pass every
        # symbolic assertion while halving the real cap).
        assert _DRAFTS_MAX_TOTAL == 500

    def test_not_authenticated_raises(self, client: GmailClient):
        client.service = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_drafts()

    def test_empty_mailbox_returns_empty(self, client: GmailClient):
        client.service = MagicMock()
        (
            client.service.users.return_value
            .drafts.return_value.list.return_value.execute
            .return_value
        ) = {"drafts": []}

        with patch.object(client, "_execute_batch_get") as mock_batch:
            result = client.fetch_drafts()

        assert result == []
        mock_batch.assert_not_called()

    def test_under_cap_fetches_all(self, client: GmailClient):
        client.service = MagicMock()
        draft_ids = [f"d{i}" for i in range(5)]
        (
            client.service.users.return_value
            .drafts.return_value.list.return_value.execute
            .return_value
        ) = {"drafts": [{"id": i} for i in draft_ids]}

        fake_responses = {i: _gmail_draft_response(i) for i in draft_ids}
        with patch.object(
            client, "_execute_batch_get", return_value=fake_responses,
        ) as mock_batch:
            result = client.fetch_drafts()

        assert len(result) == 5
        assert {r.provider_draft_id for r in result} == set(draft_ids)
        mock_batch.assert_called_once()
        _, kwargs = mock_batch.call_args
        # Verify the batch was called with resource="drafts"
        assert kwargs.get("resource") == "drafts"
        # Verify the passed IDs are exactly the 5 we listed
        passed_ids = mock_batch.call_args[0][0]
        assert passed_ids == draft_ids

    def test_caps_at_max_total_single_page(self, client: GmailClient):
        """If a single page returns > _DRAFTS_MAX_TOTAL IDs, the cap is enforced."""
        client.service = MagicMock()
        # Simulate Gmail returning 600 IDs in the first page (> the 500 cap).
        big_page = [{"id": f"d{i}"} for i in range(600)]
        (
            client.service.users.return_value
            .drafts.return_value.list.return_value.execute
            .return_value
        ) = {"drafts": big_page}

        with patch.object(
            client, "_execute_batch_get",
            side_effect=lambda ids, **kw: {i: _gmail_draft_response(i) for i in ids},
        ) as mock_batch:
            result = client.fetch_drafts()

        assert len(result) == _DRAFTS_MAX_TOTAL
        passed_ids = mock_batch.call_args[0][0]
        assert len(passed_ids) == _DRAFTS_MAX_TOTAL

    def test_caps_across_pages(self, client: GmailClient):
        """If pagination is needed, the cap stops collection across pages."""
        client.service = MagicMock()
        page_one = {"drafts": [{"id": f"d{i}"} for i in range(300)], "nextPageToken": "abc"}
        page_two = {"drafts": [{"id": f"d{i}"} for i in range(300, 600)]}
        page_three_should_not_happen = {"drafts": [{"id": "should-never-see"}]}

        call_count = {"n": 0}

        def _execute_side_effect():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return page_one
            if call_count["n"] == 2:
                return page_two
            return page_three_should_not_happen

        (
            client.service.users.return_value
            .drafts.return_value.list.return_value.execute
            .side_effect
        ) = _execute_side_effect

        with patch.object(
            client, "_execute_batch_get",
            side_effect=lambda ids, **kw: {i: _gmail_draft_response(i) for i in ids},
        ) as mock_batch:
            result = client.fetch_drafts()

        assert len(result) == _DRAFTS_MAX_TOTAL
        # Exactly 2 list calls were made (page 1 = 300, page 2 adds 200 more to reach 500).
        assert call_count["n"] == 2
        passed_ids = mock_batch.call_args[0][0]
        assert len(passed_ids) == _DRAFTS_MAX_TOTAL

    def test_list_httperror_translated(self, client: GmailClient):
        from googleapiclient.errors import HttpError as GHttpError

        class _FakeResp:
            status = 500
            reason = "Internal Server Error"

        client.service = MagicMock()
        (
            client.service.users.return_value
            .drafts.return_value.list.return_value.execute
            .side_effect
        ) = GHttpError(resp=_FakeResp(), content=b"boom")

        with pytest.raises(EmailExternalAPIError, match="failed to list drafts"):
            client.fetch_drafts()

    def test_parse_gmail_draft_extracts_headers_and_body(self, client: GmailClient):
        response = _gmail_draft_response("d1", subject="Test subject")
        draft = client._parse_gmail_draft(response)
        assert draft.provider_draft_id == "d1"
        assert draft.subject == "Test subject"
        assert draft.to_recipients == ["a@b.com", "c@d.com"]
        assert draft.cc_recipients == ["e@f.com"]
        assert draft.bcc_recipients == []
        assert "body-d1" in draft.body

    @pytest.mark.parametrize(
        "raw_html,expected",
        [
            ("<p>hello</p>\n", "<p>hello</p>"),
            ("<p>hello</p>\r\n", "<p>hello</p>"),
            ("<p>a</p><p>b</p>\n", "<p>a</p><p>b</p>"),
            ("<p>no trailing</p>", "<p>no trailing</p>"),
            ("", ""),
        ],
    )
    def test_parse_gmail_draft_strips_single_trailing_newline(
        self, client: GmailClient, raw_html: str, expected: str,
    ):
        """The MIME serialization of the text/html part appends one trailing
        newline that Gmail echoes back verbatim. ``_parse_gmail_draft`` now
        prefers the text/html part and strips exactly one terminator (``\\r\\n``
        first, then ``\\n``) so the round-tripped HTML matches what was
        composed (and stays consistent with Outlook, which never adds one)."""
        response = {
            "id": "d-nl",
            "message": {
                "id": "msg-d-nl",
                "payload": {
                    "headers": [{"name": "Subject", "value": "s"}],
                    "mimeType": "text/html",
                    "body": {"data": _encode_body(raw_html)},
                },
            },
        }
        draft = client._parse_gmail_draft(response)
        assert draft.body == expected

    def test_parse_gmail_draft_round_trips_html_body(self, client: GmailClient):
        """A draft built with ``build_mime_with_attachments`` (HTML
        multipart/alternative) round-trips: the parsed body is the composed
        HTML with no trailing newline, taken from the text/html leg."""
        from core.email import build_mime_with_attachments

        html = "<p>Hola <strong>mundo</strong></p>"
        raw = build_mime_with_attachments(
            to_recipients=["a@b.com"], cc_recipients=[], bcc_recipients=[],
            subject="S", body=html, attachments=[],
        )
        full_msg = message_from_bytes(raw, policy=policy.default)

        def _to_payload(part):
            if part.is_multipart():
                return {
                    "mimeType": part.get_content_type(),
                    "parts": [_to_payload(p) for p in part.iter_parts()],
                }
            data = part.get_payload(decode=True) or b""
            return {
                "mimeType": part.get_content_type(),
                "body": {"data": base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")},
            }

        response = {
            "id": "d-rt",
            "message": {"id": "m-rt", "payload": _to_payload(full_msg)},
        }
        draft = client._parse_gmail_draft(response)
        assert draft.body == html
        assert not draft.body.endswith("\n")

    def test_parse_gmail_draft_converts_legacy_text_plain_to_html(self, client: GmailClient):
        """A legacy draft carrying ONLY a text/plain part (pre-rich-text, or
        authored by another client) is converted to HTML via
        ``plain_text_to_html`` so the persisted body is always HTML."""
        response = {
            "id": "d-legacy",
            "message": {
                "id": "m-legacy",
                "payload": {
                    "headers": [{"name": "Subject", "value": "s"}],
                    "mimeType": "text/plain",
                    "body": {"data": _encode_body("line1\nline2")},
                },
            },
        }
        draft = client._parse_gmail_draft(response)
        assert draft.body == "<p>line1<br>line2</p>"


# ── create_draft — reply / forward extensions ──────────────────────


class TestGmailCreateDraftReply:
    """Covers ``thread_id`` + ``extra_headers`` propagation in create_draft."""

    def _setup(self, client: GmailClient, response: dict | None = None):
        mock_service = MagicMock()
        client.service = mock_service
        if response is None:
            response = {"id": "draft-1", "message": {"id": "msg-1"}}
        mock_service.users.return_value.drafts.return_value.create.return_value.execute.return_value = response
        return mock_service

    def test_thread_id_included_in_payload(self, client: GmailClient):
        # Gmail requires ``threadId`` on the request body for the reply
        # to land in the original thread.
        mock_service = self._setup(client)
        client.create_draft(
            ["to@x"], [], [], "Re: Hello", "body",
            thread_id="thread-1",
        )
        create_fn = mock_service.users.return_value.drafts.return_value.create
        body = create_fn.call_args[1]["body"]
        assert body["message"].get("threadId") == "thread-1"

    def test_no_thread_id_excludes_field_from_payload(self, client: GmailClient):
        # Standalone draft path: no threadId on the wire.
        mock_service = self._setup(client)
        client.create_draft(["to@x"], [], [], "Hello", "body")
        body = mock_service.users.return_value.drafts.return_value.create.call_args[1]["body"]
        assert "threadId" not in body["message"]

    def test_in_reply_to_and_references_propagate_to_mime(self, client: GmailClient):
        # The MIME bytes must carry the RFC 5322 headers so any destination
        # client (Outlook, Apple Mail) re-threads correctly even without
        # the Gmail-specific threadId.
        mock_service = self._setup(client)
        client.create_draft(
            ["to@x"], [], [], "Re: Hello", "body",
            in_reply_to="<orig@x>", references="<older@x> <orig@x>",
        )
        body = mock_service.users.return_value.drafts.return_value.create.call_args[1]["body"]
        # ``raw`` is base64url-encoded. Decode it back to text and verify
        # the headers are inside the MIME.
        raw_b64 = body["message"]["raw"]
        # Pad for urlsafe decode.
        padding = "=" * (4 - len(raw_b64) % 4)
        mime_bytes = base64.urlsafe_b64decode(raw_b64 + padding)
        text = mime_bytes.decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in text
        assert "References: <older@x> <orig@x>" in text
