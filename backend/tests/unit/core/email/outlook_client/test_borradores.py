"""Tests espejo de ``outlook_client.borradores`` (fetch_drafts y createReply*/createForward)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.email.email_client import DraftMetadata
from core.email.errors import (
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
    EmailRecipientsMissingError,
)
from core.email.outlook_client import GRAPH_BASE_URL, _DRAFTS_MAX_RETRIES, _DRAFTS_MAX_TOTAL
from core.email.outlook_client.borradores import _DRAFTS_PAGE_SIZE


# ── fetch_drafts ────────────────────────────────────────────────────


def _outlook_draft_json(draft_id: str, subject: str = "Hello") -> dict:
    """Build a fake Graph Message JSON for a draft."""
    return {
        "id": draft_id,
        "subject": subject,
        "body": {"contentType": "HTML", "content": f"<p>body-{draft_id}</p>"},
        "toRecipients": [{"emailAddress": {"address": "a@b.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "c@d.com"}}],
        "bccRecipients": [],
        "createdDateTime": "2024-01-01T10:00:00Z",
        "lastModifiedDateTime": "2024-01-02T11:00:00Z",
    }


class TestFetchDrafts:
    def test_cap_and_page_size_are_500(self):
        # Both the per-account draft cap and the Graph page size were raised
        # 100 -> 500 (Graph accepts $top<=1000, so a single 500-row page covers
        # the cap). Pin both constants so a silent revert can't halve the fetch
        # while every symbolic assertion below still passes.
        assert _DRAFTS_MAX_TOTAL == 500
        assert _DRAFTS_PAGE_SIZE == 500

    def test_not_authenticated_raises(self, client: OutlookClient):
        client._access_token = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_drafts()

    def test_empty_mailbox_returns_empty(self, client: OutlookClient):
        client._access_token = "tok"
        with patch.object(client, "_graph_request", return_value={"value": []}):
            result = client.fetch_drafts()
        assert result == []

    def test_under_cap_single_page(self, client: OutlookClient):
        client._access_token = "tok"
        page = {"value": [_outlook_draft_json(f"d{i}") for i in range(5)]}
        with patch.object(client, "_graph_request", return_value=page) as mock_graph:
            result = client.fetch_drafts()
        assert len(result) == 5
        assert all(isinstance(d, DraftMetadata) for d in result)
        # Only one page fetch (no nextLink)
        assert mock_graph.call_count == 1

    def test_caps_at_max_total_single_page(self, client: OutlookClient):
        """Defensive cut when a page returns more than the cap (>500) items."""
        client._access_token = "tok"
        oversize = [_outlook_draft_json(f"d{i}") for i in range(600)]
        with patch.object(client, "_graph_request", return_value={"value": oversize}):
            result = client.fetch_drafts()
        assert len(result) == _DRAFTS_MAX_TOTAL

    def test_caps_single_page_exactly_at_max(self, client: OutlookClient):
        """A single page returning exactly _DRAFTS_MAX_TOTAL items with a
        stale nextLink is sufficient to reach the cap — the loop exits
        before following nextLink because len(drafts) == max."""
        client._access_token = "tok"
        page_one = {
            "value": [_outlook_draft_json(f"d{i}") for i in range(500)],
            "@odata.nextLink": f"{GRAPH_BASE_URL}/me/mailFolders/drafts/messages?$skip=500",
        }

        call_count = {"n": 0}

        def mock_graph(method, url, **kwargs):
            call_count["n"] += 1
            return page_one

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.fetch_drafts()

        assert len(result) == _DRAFTS_MAX_TOTAL
        # Only the first page is fetched — once 500 drafts are collected,
        # the while loop exits before following nextLink.
        assert call_count["n"] == 1

    def test_caps_across_pages(self, client: OutlookClient):
        """Two pages of 300 items each: the second page is partially consumed
        (200 items) until the cap is hit, then the loop exits. Total 500
        drafts, exactly 2 _graph_request calls."""
        client._access_token = "tok"

        page_one = {
            "value": [_outlook_draft_json(f"p1-d{i}") for i in range(300)],
            "@odata.nextLink": f"{GRAPH_BASE_URL}/me/mailFolders/drafts/messages?$skip=300",
        }
        page_two = {
            "value": [_outlook_draft_json(f"p2-d{i}") for i in range(300)],
            # nextLink present but must never be followed — cap reached mid-page.
            "@odata.nextLink": f"{GRAPH_BASE_URL}/me/mailFolders/drafts/messages?$skip=600",
        }
        pages = [page_one, page_two]
        call_count = {"n": 0}

        def mock_graph(method, url, **kwargs):
            call_count["n"] += 1
            return pages.pop(0)

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            result = client.fetch_drafts()

        assert len(result) == _DRAFTS_MAX_TOTAL
        assert call_count["n"] == 2
        # First 300 come from page 1; next 200 come from page 2 (truncated).
        assert result[0].provider_draft_id == "p1-d0"
        assert result[299].provider_draft_id == "p1-d299"
        assert result[300].provider_draft_id == "p2-d0"
        assert result[499].provider_draft_id == "p2-d199"

    def test_request_url_contains_orderby_and_top(self, client: OutlookClient):
        client._access_token = "tok"
        captured_urls: list[str] = []

        def mock_graph(method, url, **kwargs):
            captured_urls.append(url)
            return {"value": []}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            client.fetch_drafts()

        assert len(captured_urls) == 1
        url = captured_urls[0]
        assert "$top=500" in url
        assert "$orderby=lastModifiedDateTime%20desc" in url
        assert "$select=" in url
        assert "id,subject,body" in url

    def test_request_uses_immutable_id_header(self, client: OutlookClient):
        client._access_token = "tok"
        captured_headers: list[dict | None] = []

        def mock_graph(method, url, **kwargs):
            captured_headers.append(kwargs.get("extra_headers"))
            return {"value": []}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            client.fetch_drafts()

        assert captured_headers == [{"Prefer": 'IdType="ImmutableId"'}]

    def test_page_retry_on_transient_error(self, client: OutlookClient):
        client._access_token = "tok"
        call_count = {"n": 0}

        def mock_graph(method, url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise EmailExternalAPIError("Transient")
            return {"value": [_outlook_draft_json("d1")]}

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            with patch("core.email.outlook_client.borradores.time.sleep"):
                result = client.fetch_drafts()

        assert len(result) == 1
        assert call_count["n"] == 3

    def test_page_retry_gives_up_after_max(self, client: OutlookClient):
        client._access_token = "tok"
        call_count = {"n": 0}

        def mock_graph(method, url, **kwargs):
            call_count["n"] += 1
            raise EmailExternalAPIError("Persistent failure")

        with patch.object(client, "_graph_request", side_effect=mock_graph):
            with patch("core.email.outlook_client.borradores.time.sleep"):
                with pytest.raises(EmailExternalAPIError, match="Persistent failure"):
                    client.fetch_drafts()

        # 1 initial attempt + _DRAFTS_MAX_RETRIES (=4) retries = 5 calls total.
        assert call_count["n"] == _DRAFTS_MAX_RETRIES + 1

    def test_parse_outlook_draft_extracts_fields(self, client: OutlookClient):
        msg = _outlook_draft_json("d1", subject="Parsed subject")
        draft = client._parse_outlook_draft(msg)
        assert draft.provider_draft_id == "d1"
        assert draft.subject == "Parsed subject"
        assert draft.body == "<p>body-d1</p>"
        assert draft.to_recipients == ["a@b.com"]
        assert draft.cc_recipients == ["c@d.com"]
        assert draft.bcc_recipients == []
        assert draft.created_at == datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert draft.updated_at == datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc)

    def test_parse_outlook_draft_flattens_graph_html_wrapper(self, client: OutlookClient):
        # Graph returns contentType=HTML wrapped in a full
        # <html><head><meta us-ascii></head><body>…</body></html> document;
        # the parser flattens it to the clean <body> fragment to seed the
        # composer.
        msg = {
            "id": "d-html",
            "subject": "s",
            "body": {
                "contentType": "HTML",
                "content": (
                    "<html><head><meta charset=us-ascii></head>"
                    "<body><p>hi</p></body></html>"
                ),
            },
            "toRecipients": [],
            "ccRecipients": [],
            "bccRecipients": [],
        }
        draft = client._parse_outlook_draft(msg)
        assert draft.body == "<p>hi</p>"

    def test_parse_outlook_draft_converts_legacy_text_to_html(self, client: OutlookClient):
        # A legacy draft stored as contentType=Text is converted to HTML via
        # plain_text_to_html so the persisted body is always HTML.
        msg = {
            "id": "d-legacy",
            "subject": "s",
            "body": {"contentType": "Text", "content": "line1\nline2"},
            "toRecipients": [],
            "ccRecipients": [],
            "bccRecipients": [],
        }
        draft = client._parse_outlook_draft(msg)
        assert draft.body == "<p>line1<br>line2</p>"


# ── _create_draft_via_reply — body shape + endpoint routing ────────


class TestOutlookCreateDraftViaReply:
    """Covers ``createReply`` / ``createReplyAll`` / ``createForward`` routing.

    Per §4.2 + R-09: the body JSON carries ONLY ``message`` (no ``comment``,
    no root-level ``toRecipients`` — XOR constraint guaranteed 400).
    Pre-validation of ``toRecipients`` for ``reply`` / ``reply_all``
    locally rejects empty recipients to avoid burning provider quota.
    """

    _DRAFT_RESPONSE = {
        "id": "draft-1",
        "createdDateTime": "2026-05-23T14:32:00Z",
        "lastModifiedDateTime": "2026-05-23T14:32:00Z",
    }

    def test_reply_endpoint_path_uses_create_reply(self, authenticated_client):
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = body
            captured["extra_headers"] = extra_headers
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                ["to@x"], [], [], "Re: Hi", "body",
                reply_to_message_id="orig-1", reply_kind="reply",
            )
        assert "createReply" in captured["url"]
        # Must NOT use createReplyAll / createForward.
        assert "createReplyAll" not in captured["url"]
        assert "createForward" not in captured["url"]

    def test_reply_all_endpoint_path(self, authenticated_client):
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["url"] = url
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                ["to@x"], ["cc@x"], [], "Re: Hi", "body",
                reply_to_message_id="orig-1", reply_kind="reply_all",
            )
        assert "createReplyAll" in captured["url"]

    def test_forward_endpoint_path(self, authenticated_client):
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["url"] = url
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                [], [], [], "Fwd: Hi", "body",
                reply_to_message_id="orig-1", reply_kind="forward",
            )
        assert "createForward" in captured["url"]

    def test_body_shape_only_message_no_comment_no_root_to(self, authenticated_client):
        # Graph's XOR constraint: ``comment`` + ``message.body`` →
        # 400; root ``toRecipients`` + ``message.toRecipients`` → 400.
        # The helper MUST send only ``{"message": {...}}``.
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["body"] = body
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                ["to@x"], ["cc@x"], [], "Re: Hi", "Plain body",
                reply_to_message_id="orig-1", reply_kind="reply",
            )
        body = captured["body"]
        # Shape: only ``message`` key at the root.
        assert set(body.keys()) == {"message"}
        # ``comment`` (the rich-text alternative) is NEVER present.
        assert "comment" not in body
        # Root-level ``toRecipients`` is NEVER present.
        assert "toRecipients" not in body
        # The composer fields ride inside ``message`` only.
        message = body["message"]
        assert message["subject"] == "Re: Hi"
        # Body is now HTML (the composer emits sanitised HTML; Graph stores it
        # as contentType=HTML and re-wraps it on read).
        assert message["body"] == {"contentType": "HTML", "content": "Plain body"}
        assert message["toRecipients"] == [{"emailAddress": {"address": "to@x"}}]
        assert message["ccRecipients"] == [{"emailAddress": {"address": "cc@x"}}]

    def test_prefer_immutable_id_header_sent(self, authenticated_client):
        # ``Prefer: IdType="ImmutableId"`` must travel with every Graph
        # call (the stored id is an Immutable ID — without the header
        # Graph reinterprets it as transient and returns 404).
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["extra_headers"] = extra_headers
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                ["to@x"], [], [], "Re: Hi", "body",
                reply_to_message_id="orig-1", reply_kind="reply",
            )
        # The header dict is propagated. The value is the canonical
        # ``IdType="ImmutableId"`` (production constant in outlook_client.py).
        assert captured["extra_headers"] is not None
        any_immutable = any(
            "ImmutableId" in str(v) or "Prefer" in str(k)
            for k, v in (captured["extra_headers"] or {}).items()
        )
        assert any_immutable

    def test_reply_with_empty_recipients_raises_locally(self, authenticated_client):
        # The pre-check rejects ``reply`` / ``reply_all`` with empty
        # toRecipients BEFORE the Graph call (the constraint would 400).
        with patch.object(authenticated_client, "_graph_request") as graph_mock:
            with pytest.raises(EmailRecipientsMissingError):
                authenticated_client.create_draft(
                    [], [], [], "Re: Hi", "body",
                    reply_to_message_id="orig-1", reply_kind="reply",
                )
            graph_mock.assert_not_called()

    def test_reply_all_with_empty_recipients_raises_locally(self, authenticated_client):
        with patch.object(authenticated_client, "_graph_request") as graph_mock:
            with pytest.raises(EmailRecipientsMissingError):
                authenticated_client.create_draft(
                    [], ["cc@x"], [], "Re: Hi", "body",
                    reply_to_message_id="orig-1", reply_kind="reply_all",
                )
            graph_mock.assert_not_called()

    def test_forward_with_empty_recipients_makes_call(self, authenticated_client):
        # Forward is the documented exception (R-07): the composer fills
        # recipients later, so the create-draft path tolerates empty.
        with patch.object(
            authenticated_client, "_graph_request",
            return_value=self._DRAFT_RESPONSE,
        ) as graph_mock:
            authenticated_client.create_draft(
                [], [], [], "Fwd: Hi", "body",
                reply_to_message_id="orig-1", reply_kind="forward",
            )
            graph_mock.assert_called_once()

    def test_no_reply_to_message_id_uses_post_me_messages_path(self, authenticated_client):
        # Plain draft (no reply context) falls back to the legacy
        # ``POST /me/messages`` path.
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["url"] = url
            return self._DRAFT_RESPONSE

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            authenticated_client.create_draft(
                ["to@x"], [], [], "Hi", "body",
            )
        # No ``createReply`` / ``createForward`` markers.
        assert "createReply" not in captured["url"]
        assert "createForward" not in captured["url"]
        # The path ends in ``/me/messages`` (no id segment).
        assert captured["url"].endswith("/me/messages")
