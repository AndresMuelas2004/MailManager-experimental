"""Tests espejo de ``outlook_client.contenido`` (cuerpo+cid, adjuntos, reply context y conversaciones)."""

from __future__ import annotations

import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.outlook_client import OutlookClient, _parse_graph_datetime

from ._helpers import _make_authenticated_client


# ── fetch_email_content + cid resolution ────────────────────────────


class TestFetchEmailContentInlineImages:
    def _make_authed_client(self):
        client = OutlookClient(account_label="mb__outlook")
        client._access_token = "tok"
        return client

    def test_html_with_attachments_resolves_cid(self):
        client = self._make_authed_client()
        responses = [
            {
                "body": {"contentType": "html", "content": '<img src="cid:logo@x">'},
                "hasAttachments": True,
            },
            {
                "value": [
                    {
                        "isInline": True,
                        "contentId": "logo@x",
                        "contentType": "image/png",
                        "contentBytes": "QUFB",
                    },
                ],
            },
        ]
        client._graph_request = MagicMock(side_effect=responses)
        content, _attachments, cid_map = client.fetch_content_with_attachments("mid")
        assert 'src="data:image/png;base64,QUFB"' in content.html_body
        assert client._graph_request.call_count == 2
        # The referenced inline image surfaces in the cid_map (third tuple element).
        assert "logo@x" in cid_map

    def test_html_classifies_attachments_even_when_has_attachments_false(self):
        # ``hasAttachments`` is ``false`` when a message carries ONLY inline
        # images, so the unified read must NOT gate classification on it
        # (the old fetch_email_content did, and skipped resolving the cid:).
        # The second GET (the attachments listing) therefore ALWAYS fires for
        # an HTML body, and the inline image resolves.
        client = self._make_authed_client()
        responses = [
            {
                "body": {"contentType": "html", "content": '<img src="cid:logo@x">'},
                "hasAttachments": False,
            },
            {
                "value": [
                    {
                        "isInline": True,
                        "contentId": "logo@x",
                        "contentType": "image/png",
                        "contentBytes": "QUFB",
                    },
                ],
            },
        ]
        client._graph_request = MagicMock(side_effect=responses)
        content, _attachments, _cid_map = client.fetch_content_with_attachments("mid")
        assert 'src="data:image/png;base64,QUFB"' in content.html_body
        # Classification ran despite hasAttachments=false → second GET fired.
        assert client._graph_request.call_count == 2

    def test_plain_text_skips_cid_resolution(self):
        client = self._make_authed_client()
        client._graph_request = MagicMock(
            return_value={
                "body": {"contentType": "text", "content": "plain"},
                "hasAttachments": True,
            },
        )
        content, attachments, cid_map = client.fetch_content_with_attachments("mid")
        assert content.html_body is None
        assert content.text_body == "plain"
        # A non-HTML body is NOT classified (no cid: semantics) → no second GET,
        # empty attachments + cid_map (preserves the previous behaviour).
        assert client._graph_request.call_count == 1
        assert attachments == []
        assert cid_map == {}

    def test_attachments_fetch_error_soft_fallback(self):
        client = self._make_authed_client()
        side = [
            {
                "body": {"contentType": "html", "content": '<img src="cid:logo">'},
                "hasAttachments": True,
            },
            EmailExternalAPIError("graph blew up"),
        ]

        def fake(*args, **kwargs):
            value = side.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        client._graph_request = MagicMock(side_effect=fake)
        content, attachments, cid_map = client.fetch_content_with_attachments("mid")
        # Soft fallback: cid reference kept intact, no propagation; the failed
        # attachments GET collapses to empty classification.
        assert 'src="cid:logo"' in content.html_body
        assert attachments == []
        assert cid_map == {}

    def test_non_image_inline_attachment_skipped(self):
        client = self._make_authed_client()
        responses = [
            {
                "body": {"contentType": "html", "content": '<img src="cid:doc">'},
                "hasAttachments": True,
            },
            {
                "value": [
                    {
                        "isInline": True,
                        "contentId": "doc",
                        "contentType": "application/pdf",
                        "contentBytes": "QUFB",
                    },
                ],
            },
        ]
        client._graph_request = MagicMock(side_effect=responses)
        content, _attachments, _cid_map = client.fetch_content_with_attachments("mid")
        assert 'src="cid:doc"' in content.html_body


class TestOutlookFetchAttachmentBinary:
    def _attachment(self):
        from core.email.email_client import AttachmentMetadata
        return AttachmentMetadata(
            provider_message_id="msg-1",
            part_id=None,
            provider_attachment_id="att-1",
            filename="doc.pdf",
            mime_type="application/pdf",
            size=10,
            content_id=None,
            is_inline=False,
            position=0,
        )

    def test_200_returns_decoded_binary(self, authenticated_client, monkeypatch):
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: (200, {}, b"hello"),
        )
        binary = authenticated_client.fetch_attachment_binary("msg-1", self._attachment())
        assert binary.data == b"hello"
        assert binary.size == 5
        assert binary.mime_type == "application/pdf"

    def test_404_raises_attachment_not_found(self, authenticated_client, monkeypatch):
        from core.email.errors import EmailAttachmentNotFound
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: (404, {}, b""),
        )
        with pytest.raises(EmailAttachmentNotFound):
            authenticated_client.fetch_attachment_binary("msg-1", self._attachment())

    def test_410_raises_attachment_not_found(self, authenticated_client, monkeypatch):
        from core.email.errors import EmailAttachmentNotFound
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: (410, {}, b""),
        )
        with pytest.raises(EmailAttachmentNotFound):
            authenticated_client.fetch_attachment_binary("msg-1", self._attachment())

    def test_403_raises_forbidden_reason(self, authenticated_client, monkeypatch):
        from core.email.errors import EmailAttachmentDownloadFailed
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: (403, {}, b""),
        )
        with pytest.raises(EmailAttachmentDownloadFailed) as exc_info:
            authenticated_client.fetch_attachment_binary("msg-1", self._attachment())
        assert exc_info.value.detail.get("reason") == "forbidden"

    def test_persistent_5xx_after_retries_raises_unavailable(self, authenticated_client, monkeypatch):
        from core.email.errors import EmailAttachmentDownloadFailed
        # Speed up the test by collapsing sleep delays.
        monkeypatch.setattr("core.email.outlook_client.contenido.time.sleep", lambda _s: None)
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: (503, {}, b""),
        )
        with pytest.raises(EmailAttachmentDownloadFailed) as exc_info:
            authenticated_client.fetch_attachment_binary("msg-1", self._attachment())
        assert exc_info.value.detail.get("reason") == "unavailable"

    def test_429_then_200_succeeds_after_retry(self, authenticated_client, monkeypatch):
        monkeypatch.setattr("core.email.outlook_client.contenido.time.sleep", lambda _s: None)
        responses = iter([
            (429, {"Retry-After": "1"}, b""),
            (200, {}, b"ok"),
        ])
        monkeypatch.setattr(
            authenticated_client, "_graph_request_raw",
            lambda method, url, extra_headers=None: next(responses),
        )
        binary = authenticated_client.fetch_attachment_binary("msg-1", self._attachment())
        assert binary.data == b"ok"

    def test_url_error_treated_as_503_and_retried(self, authenticated_client, monkeypatch):
        # Phase 2.3 fix: URLError must be folded into the retry loop as a
        # synthetic 503 instead of escaping immediately as
        # EmailExternalAPIError. Verifying the retry continues until success.
        monkeypatch.setattr("core.email.outlook_client.contenido.time.sleep", lambda _s: None)
        # Build a real URLError + a successful follow-up so the retry path
        # is exercised end-to-end via the production _graph_request_raw.
        call_count = {"n": 0}

        def fake_urlopen(req, timeout):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise urllib.error.URLError("connection refused")
            response = MagicMock()
            response.status = 200
            response.headers = {}
            response.read.return_value = b"recovered"
            response.__enter__ = lambda self: self
            response.__exit__ = lambda self, *a: None
            return response

        monkeypatch.setattr(
            "core.email.outlook_client.transporte.urllib.request.urlopen", fake_urlopen,
        )
        binary = authenticated_client.fetch_attachment_binary("msg-1", self._attachment())
        assert binary.data == b"recovered"


class TestOutlookListMessageAttachments:
    def test_returns_classification_tuple(self, authenticated_client, monkeypatch):
        from core.email.email_client import AttachmentMetadata
        sample = AttachmentMetadata(
            provider_message_id="msg-1",
            part_id=None,
            provider_attachment_id="att-1",
            filename="doc.pdf",
            mime_type="application/pdf",
            size=10,
            content_id=None,
            is_inline=False,
            position=0,
        )
        # list_message_attachments first calls _graph_request to fetch the
        # body (for cid resolution), then _classify_attachments. Stub both.
        monkeypatch.setattr(
            authenticated_client, "_graph_request",
            lambda method, url, body=None, extra_headers=None: {
                "body": {"contentType": "html", "content": "<p>hi</p>"},
            },
        )
        monkeypatch.setattr(
            authenticated_client, "_classify_attachments",
            lambda escaped_id, html_body, *, provider_message_id=None: (
                {"cid-x": "data:image/png;base64,YQ=="}, [sample],
            ),
        )
        attachments, cid_map = authenticated_client.list_message_attachments("msg-1")
        assert attachments == [sample]
        assert cid_map == {"cid-x": "data:image/png;base64,YQ=="}


# ── fetch_reply_context — Graph payload parsing ────────────────────


class TestOutlookFetchReplyContext:
    """Covers the GET /me/messages parsing in fetch_reply_context."""

    def _graph_message(self, *, parent_folder_id: str = "inbox-folder-1"):
        return {
            "id": "msg-1",
            "from": {
                "emailAddress": {"address": "ana@example.com", "name": "Ana Lopez"},
            },
            "toRecipients": [
                {"emailAddress": {"address": "me@me.com", "name": "Me"}},
                {"emailAddress": {"address": "carol@x.com"}},
            ],
            "ccRecipients": [
                {"emailAddress": {"address": "dan@y.com"}},
            ],
            "replyTo": [
                {"emailAddress": {"address": "editor@list.com"}},
            ],
            "subject": "Hello",
            "body": {"contentType": "html", "content": "<p>body</p>"},
            "internetMessageId": "<orig@x>",
            "internetMessageHeaders": [
                {"name": "References", "value": "<older@x>"},
                {"name": "X-Other", "value": "ignored"},
            ],
            "receivedDateTime": "2026-05-23T14:32:00Z",
            "conversationId": "conv-1",
            "parentFolderId": parent_folder_id,
            "hasAttachments": False,
        }

    def test_parses_graph_message_into_reply_context(self, authenticated_client):
        msg = self._graph_message()
        # Stub the GET + folder resolution.
        with patch.object(authenticated_client, "_graph_request", return_value=msg):
            with patch.object(
                authenticated_client, "_resolve_special_folder_ids",
                return_value={"inbox-folder-1": "ALL_MAIL"},
            ):
                result = authenticated_client.fetch_reply_context("msg-1")
        assert result.provider_message_id == "msg-1"
        assert result.thread_id == "conv-1"
        assert result.from_email == "ana@example.com"
        assert result.from_name == "Ana Lopez"
        assert result.reply_to == ["editor@list.com"]
        assert result.to_recipients == ["me@me.com", "carol@x.com"]
        assert result.cc_recipients == ["dan@y.com"]
        assert result.subject == "Hello"
        # Body shape: html part goes to body_html.
        assert result.body_html == "<p>body</p>"
        assert result.body_text is None
        # Internet message id has angle brackets stripped at parse time.
        assert result.message_id == "orig@x"
        # References extracted from internetMessageHeaders by case-insensitive name.
        assert result.references == "<older@x>"

    def test_parent_folder_resolved_to_box(self, authenticated_client):
        # Parent folder id resolution → "SENT" mapping → ReplyContext.box.
        msg = self._graph_message(parent_folder_id="sent-folder-1")
        with patch.object(authenticated_client, "_graph_request", return_value=msg):
            with patch.object(
                authenticated_client, "_resolve_special_folder_ids",
                return_value={"sent-folder-1": "SENT"},
            ):
                result = authenticated_client.fetch_reply_context("msg-1")
        assert result.box == "SENT"

    def test_unknown_folder_collapses_to_all_mail(self, authenticated_client):
        # Defensive: a parent folder id we don't recognise must NOT crash
        # — the box just collapses to ALL_MAIL.
        msg = self._graph_message(parent_folder_id="unknown-folder-zzz")
        with patch.object(authenticated_client, "_graph_request", return_value=msg):
            with patch.object(
                authenticated_client, "_resolve_special_folder_ids",
                return_value={},
            ):
                result = authenticated_client.fetch_reply_context("msg-1")
        assert result.box == "ALL_MAIL"

    def test_prefer_immutable_id_header_sent(self, authenticated_client):
        # The GET must carry ``Prefer: IdType="ImmutableId"`` (same rule
        # as every other call against an immutable-id endpoint).
        captured = {}

        def _graph_request(method, url, body=None, extra_headers=None):
            captured["extra_headers"] = extra_headers
            return self._graph_message()

        with patch.object(authenticated_client, "_graph_request", side_effect=_graph_request):
            with patch.object(
                authenticated_client, "_resolve_special_folder_ids",
                return_value={},
            ):
                authenticated_client.fetch_reply_context("msg-1")
        any_immutable = any(
            "ImmutableId" in str(v) or "Prefer" in str(k)
            for k, v in (captured.get("extra_headers") or {}).items()
        )
        assert any_immutable

    def test_provider_failure_wrapped_as_reply_context_error(self, authenticated_client):
        # Failed Graph call → EmailExternalAPIError, re-wrapped by
        # ``fetch_reply_context`` as EmailReplyContextFetchError.
        from core.email.errors import EmailReplyContextFetchError
        with patch.object(
            authenticated_client, "_graph_request",
            side_effect=EmailExternalAPIError("Graph 404"),
        ):
            with pytest.raises(EmailReplyContextFetchError) as exc_info:
                authenticated_client.fetch_reply_context("msg-1")
            assert exc_info.value.detail.get("reason") == "provider_fetch_failed"

    def test_unauthenticated_raises(self, authenticated_client):
        authenticated_client._access_token = None
        from core.email.errors import EmailNotAuthenticatedError
        with pytest.raises(EmailNotAuthenticatedError):
            authenticated_client.fetch_reply_context("msg-1")

    def test_text_body_shape(self, authenticated_client):
        # contentType=Text → result.body_text populated, body_html=None.
        msg = self._graph_message()
        msg["body"] = {"contentType": "text", "content": "plain body"}
        with patch.object(authenticated_client, "_graph_request", return_value=msg):
            with patch.object(
                authenticated_client, "_resolve_special_folder_ids",
                return_value={},
            ):
                result = authenticated_client.fetch_reply_context("msg-1")
        assert result.body_text == "plain body"
        assert result.body_html is None


class TestClassifyAttachments:
    """D-13 strict inline-vs-attachment rule for Outlook (M14).

    Four conditions must ALL hold for a part to stay inline: ``isInline``,
    a ``contentId`` referenced by the body, non-empty ``contentBytes`` and an
    ``image/*`` content type. Anything else surfaces as a downloadable.
    """

    @staticmethod
    def _att(
        *, att_id="att1", name="file", content_type, content_bytes="UE5H",
        cid=None, is_inline=False, size=3,
    ):
        a: dict = {
            "id": att_id, "name": name, "contentType": content_type,
            "size": size, "isInline": is_inline,
        }
        if content_bytes is not None:
            a["contentBytes"] = content_bytes
        if cid is not None:
            a["contentId"] = f"<{cid}>"
        return a

    def test_inline_image_referenced_goes_to_cid_map(self, client: OutlookClient):
        value = [self._att(content_type="image/png", cid="logo123", is_inline=True)]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", '<img src="cid:logo123">', provider_message_id="msg-1",
            )
        assert "logo123" in cid_map
        assert cid_map["logo123"].startswith("data:image/png;base64,")
        assert downloadable == []

    def test_inline_image_with_case_mismatched_cid_still_matches(self, client: OutlookClient):
        # Graph ``contentId`` ``Logo123`` vs HTML ``cid:logo123`` — the match
        # runs on the ``normalize_cid`` form (case / percent-encoding
        # tolerant), so the image stays inline.
        value = [self._att(content_type="image/png", cid="Logo123", is_inline=True)]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", '<img src="cid:logo123">', provider_message_id="msg-1",
            )
        assert "logo123" in cid_map
        assert downloadable == []

    def test_inline_marked_unreferenced_promoted_to_downloadable(self, client: OutlookClient):
        value = [self._att(content_type="image/png", cid="orphan", is_inline=True)]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", "<p>no inline reference</p>", provider_message_id="msg-1",
            )
        assert cid_map == {}
        assert len(downloadable) == 1
        assert downloadable[0].is_inline is True
        assert downloadable[0].content_id == "orphan"

    def test_pdf_with_content_id_is_downloadable(self, client: OutlookClient):
        value = [self._att(
            name="invoice.pdf", content_type="application/pdf",
            cid="pdfcid", is_inline=True,
        )]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", '<img src="cid:pdfcid">', provider_message_id="msg-1",
            )
        assert "pdfcid" not in cid_map
        assert len(downloadable) == 1
        assert downloadable[0].filename == "invoice.pdf"
        assert downloadable[0].mime_type == "application/pdf"

    def test_inline_image_without_bytes_is_downloadable(self, client: OutlookClient):
        value = [self._att(
            content_type="image/png", cid="nobytes", is_inline=True,
            content_bytes=None,
        )]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", '<img src="cid:nobytes">', provider_message_id="msg-1",
            )
        assert cid_map == {}
        assert len(downloadable) == 1

    def test_generic_declared_type_overridden_by_extension(self, client: OutlookClient):
        # B-MIME: a generic ``application/octet-stream`` declared type with a
        # recognisable Office extension resolves to the real Office type.
        value = [self._att(
            name="hoja.xlsx", content_type="application/octet-stream",
        )]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", "<p>body</p>", provider_message_id="msg-1",
            )
        assert cid_map == {}
        assert len(downloadable) == 1
        assert downloadable[0].filename == "hoja.xlsx"
        assert (
            downloadable[0].mime_type
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_specific_declared_type_case_is_preserved(self, client: OutlookClient):
        # B-OUTLOOK-LOWER: a specific declared type is stored with its case
        # intact — the historical forced ``.lower()`` is gone.
        value = [self._att(
            name="logo.png", content_type="image/PNG", is_inline=False,
        )]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", "<p>body</p>", provider_message_id="msg-1",
            )
        assert cid_map == {}
        assert len(downloadable) == 1
        assert downloadable[0].mime_type == "image/PNG"

    def test_inline_image_with_uppercase_type_still_goes_to_cid_map(self, client: OutlookClient):
        # Edge case: the inline-image guard uses ``content_type.lower()
        # .startswith("image/")``, so an ``IMAGE/PNG`` referenced inline
        # image is still resolved into the cid_map even though the stored
        # type keeps its original case.
        value = [self._att(
            name="logo.png", content_type="IMAGE/PNG", cid="logo123", is_inline=True,
        )]
        with patch.object(client, "_graph_request", return_value={"value": value}):
            cid_map, downloadable = client._classify_attachments(
                "msg-1", '<img src="cid:logo123">', provider_message_id="msg-1",
            )
        assert "logo123" in cid_map
        assert cid_map["logo123"].startswith("data:IMAGE/PNG;base64,")
        assert downloadable == []


# ── fetch_conversation ──────────────────────────────────────────────


class TestFetchConversation:
    """Outlook conversation viewer — $filter=conversationId → members."""

    _FOLDER_MAP = {"id-sent": "SENT", "id-trash": "TRASH", "id-spam": "SPAM"}

    @staticmethod
    def _conv_message(
        msg_id: str,
        *,
        received: str | None = "2025-06-01T12:00:00Z",
        sent: str | None = None,
        parent_folder_id: str = "",
        flag_status: str | None = None,
        is_read: bool = True,
        conversation_id: str = "conv1",
    ) -> dict:
        msg: dict = {
            "id": msg_id,
            "conversationId": conversation_id,
            "from": {"emailAddress": {"address": "a@x.com", "name": "A"}},
            "subject": "Hello",
            "isRead": is_read,
        }
        if received is not None:
            msg["receivedDateTime"] = received
        if sent is not None:
            msg["sentDateTime"] = sent
        if parent_folder_id:
            msg["parentFolderId"] = parent_folder_id
        if flag_status is not None:
            msg["flag"] = {"flagStatus": flag_status}
        return msg

    def test_not_authenticated_raises(self, client: OutlookClient):
        assert client._access_token is None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_conversation("conv1")

    def test_parses_members_box_and_favorite(self):
        client = _make_authenticated_client()
        messages = [
            self._conv_message("m1", parent_folder_id="id-inbox", flag_status="flagged"),
            self._conv_message("m2", parent_folder_id="id-sent", flag_status="notFlagged"),
            self._conv_message("m3", parent_folder_id="id-spam"),
        ]
        with patch.object(client, "_resolve_special_folder_ids", return_value=self._FOLDER_MAP), \
             patch.object(client, "_graph_request", return_value={"value": messages}):
            members = client.fetch_conversation("conv1")

        by_id = {m.provider_message_id: m for m in members}
        # box derives from parentFolderId via the same folder map as sync.
        assert by_id["m1"].box == "ALL_MAIL"
        assert by_id["m2"].box == "SENT"
        assert by_id["m3"].box == "SPAM"
        # favourite derives from flag.flagStatus == "flagged".
        assert by_id["m1"].is_favorite is True
        assert by_id["m2"].is_favorite is False
        assert by_id["m3"].is_favorite is False
        assert by_id["m1"].thread_id == "conv1"
        assert by_id["m1"].account_id == ""

    def test_percent_encodes_conversation_id_and_omits_orderby(self):
        client = _make_authenticated_client()
        captured: dict = {}

        def _capture(method, url, extra_headers=None):
            captured["url"] = url
            captured["headers"] = extra_headers
            return {"value": []}

        with patch.object(client, "_resolve_special_folder_ids", return_value={}), \
             patch.object(client, "_graph_request", side_effect=_capture):
            client.fetch_conversation("AAk/ABB+id==")

        url = captured["url"]
        # base64 conversationId chars must be percent-encoded exactly once and
        # stay a single quoted $filter value.
        assert "%2F" in url  # '/'
        assert "%2B" in url  # '+'
        assert "%3D" in url  # '='
        assert "/" not in url.split("conversationId%20eq%20'")[1].split("'")[0]
        # $orderby would trigger Graph 400 InefficientFilter with this filter.
        assert "$orderby" not in url
        assert "conversationId" in url

    def test_uses_sent_date_when_received_missing(self):
        client = _make_authenticated_client()
        # A Sent-folder message often lacks receivedDateTime; the ordering
        # timestamp must fall back to sentDateTime so it sorts correctly.
        messages = [
            self._conv_message(
                "m-sent", received=None, sent="2025-06-02T08:00:00Z",
                parent_folder_id="id-sent",
            ),
            self._conv_message(
                "m-inbox", received="2025-06-01T08:00:00Z", parent_folder_id="id-inbox",
            ),
        ]
        with patch.object(client, "_resolve_special_folder_ids", return_value=self._FOLDER_MAP), \
             patch.object(client, "_graph_request", return_value={"value": messages}):
            members = client.fetch_conversation("conv1")

        sent = next(m for m in members if m.provider_message_id == "m-sent")
        assert sent.received_at == _parse_graph_datetime("2025-06-02T08:00:00Z")
        # Ascending order: the inbox message (older) precedes the sent one.
        assert [m.provider_message_id for m in members] == ["m-inbox", "m-sent"]

    def test_follows_pagination(self):
        client = _make_authenticated_client()
        page1 = {
            "value": [self._conv_message("m1", received="2025-06-01T08:00:00Z")],
            "@odata.nextLink": "https://graph/next-page",
        }
        page2 = {
            "value": [self._conv_message("m2", received="2025-06-02T08:00:00Z")],
        }
        with patch.object(client, "_resolve_special_folder_ids", return_value={}), \
             patch.object(client, "_graph_request", side_effect=[page1, page2]):
            members = client.fetch_conversation("conv1")
        assert {m.provider_message_id for m in members} == {"m1", "m2"}

    def test_empty_value_returns_empty_list(self):
        # A purged/deleted Outlook conversation returns value: [] (not 404) →
        # an empty member list (ConversationOut(messages=[]) at the service).
        client = _make_authenticated_client()
        with patch.object(client, "_resolve_special_folder_ids", return_value={}), \
             patch.object(client, "_graph_request", return_value={"value": []}):
            assert client.fetch_conversation("conv-gone") == []

    def test_skips_unparseable_member_without_aborting(self):
        client = _make_authenticated_client()
        messages = [
            self._conv_message("ok", received="2025-06-01T08:00:00Z"),
            # ``from`` as a string blows up _parse_graph_message; skip it.
            {"id": "bad", "conversationId": "conv1", "from": "not-a-dict",
             "subject": "x", "receivedDateTime": "2025-06-02T08:00:00Z", "isRead": True},
        ]
        with patch.object(client, "_resolve_special_folder_ids", return_value={}), \
             patch.object(client, "_graph_request", return_value={"value": messages}):
            members = client.fetch_conversation("conv1")
        assert [m.provider_message_id for m in members] == ["ok"]
