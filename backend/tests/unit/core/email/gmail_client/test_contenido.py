"""Tests espejo de ``gmail_client.contenido`` (cuerpo+cid, adjuntos, reply context y conversaciones)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from core.email.errors import EmailExternalAPIError, EmailNotAuthenticatedError
from core.email.gmail_client import GmailClient


# ── fetch_email_content + cid resolution ────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class TestFetchEmailContentCidResolution:
    def _build_client_with_payload(self, payload: dict) -> GmailClient:
        client = GmailClient(account_label="mb__acct")
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            "payload": payload,
        }
        client.service = service
        return client

    def test_collects_inline_image_with_inline_data(self):
        image_bytes = b"\x89PNG fake"
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b'<img src="cid:logo@x">')},
                },
                {
                    "mimeType": "image/png",
                    "headers": [{"name": "Content-ID", "value": "<logo@x>"}],
                    "body": {"data": _b64url(image_bytes)},
                },
            ],
        }
        client = self._build_client_with_payload(payload)
        content, _attachments, _cid_map = client.fetch_content_with_attachments("msg1")
        assert content.html_body is not None
        assert "cid:" not in content.html_body
        assert "data:image/png;base64," in content.html_body

    def test_fetches_attachment_when_data_missing(self):
        image_bytes = b"jpeg-bytes"
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b'<img src="cid:banner">')},
                },
                {
                    "mimeType": "image/jpeg",
                    "headers": [{"name": "content-id", "value": "banner"}],
                    "body": {"attachmentId": "att-1"},
                },
            ],
        }
        client = self._build_client_with_payload(payload)
        (
            client.service.users.return_value.messages.return_value
            .attachments.return_value.get.return_value.execute.return_value
        ) = {"data": _b64url(image_bytes)}
        content, _attachments, _cid_map = client.fetch_content_with_attachments("msg1")
        assert "data:image/jpeg;base64," in content.html_body

    def test_skips_image_without_content_id(self):
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b'<p>hi</p>')},
                },
                {
                    "mimeType": "image/png",
                    "headers": [],
                    "body": {"data": _b64url(b"x")},
                },
            ],
        }
        client = self._build_client_with_payload(payload)
        content, _attachments, _cid_map = client.fetch_content_with_attachments("msg1")
        assert "<p>hi</p>" in content.html_body

    def test_soft_fallback_on_attachment_fetch_error(self):
        from googleapiclient.errors import HttpError

        class _FakeResp:
            status = 500
            reason = "err"

        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b'<img src="cid:broken">')},
                },
                {
                    "mimeType": "image/png",
                    "headers": [{"name": "Content-ID", "value": "<broken>"}],
                    "body": {"attachmentId": "att-err"},
                },
            ],
        }
        client = self._build_client_with_payload(payload)
        (
            client.service.users.return_value.messages.return_value
            .attachments.return_value.get.return_value.execute.side_effect
        ) = HttpError(resp=_FakeResp(), content=b"boom")
        content, _attachments, _cid_map = client.fetch_content_with_attachments("msg1")
        # Soft fallback: the cid: reference is left intact, no exception raised
        assert 'src="cid:broken"' in content.html_body

    def test_no_inline_images_leaves_html_untouched(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64url(b'<p>plain html</p>')},
        }
        client = self._build_client_with_payload(payload)
        content, attachments, cid_map = client.fetch_content_with_attachments("msg1")
        assert content.html_body == "<p>plain html</p>"
        # No inline images and no downloadable parts → empty triple tail.
        assert attachments == []
        assert cid_map == {}

    def test_classifies_downloadable_attachment_when_body_is_text_only(self):
        # ``_classify_attachments`` runs UNCONDITIONALLY in the unified read
        # (the old fetch_email_content guarded it behind ``if html_body``). A
        # text-only body that still carries a downloadable part must surface
        # that attachment in the second tuple element — otherwise pre-cached
        # text mail would hide its clip forever (the cache hit never
        # re-discovers attachments).
        pdf_bytes = b"%PDF-1.4 fake"
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64url(b"just text, no html")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "headers": [
                        {"name": "Content-Disposition", "value": 'attachment; filename="invoice.pdf"'},
                    ],
                    "body": {"attachmentId": "att-pdf", "size": len(pdf_bytes)},
                },
            ],
        }
        client = self._build_client_with_payload(payload)
        content, attachments, cid_map = client.fetch_content_with_attachments("msg1")
        # Body is text-only (no HTML), yet the PDF is still classified as a
        # downloadable attachment.
        assert content.html_body is None
        assert content.text_body == "just text, no html"
        assert len(attachments) == 1
        assert attachments[0].filename == "invoice.pdf"
        assert attachments[0].is_inline is False
        # No inline images referenced → empty cid_map.
        assert cid_map == {}


# ── _extract_body_from_payload + charset detection ──────────────────


class TestExtractBodyFromPayloadCharset:
    def test_respects_content_type_charset_iso_8859_1(self):
        latin1_bytes = "España".encode("iso-8859-1")
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/html",
                "headers": [
                    {"name": "Content-Type", "value": 'text/html; charset="ISO-8859-1"'},
                ],
                "body": {"data": _b64url(latin1_bytes)},
            }],
        }
        html_body, _ = GmailClient._extract_body_from_payload(payload)
        assert html_body == "España"

    def test_defaults_to_utf8_when_no_charset_header(self):
        utf8_bytes = "España".encode("utf-8")
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/html",
                "body": {"data": _b64url(utf8_bytes)},
            }],
        }
        html_body, _ = GmailClient._extract_body_from_payload(payload)
        assert html_body == "España"

    def test_unknown_charset_falls_back_to_utf8(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/html",
                "headers": [
                    {"name": "Content-Type", "value": 'text/html; charset="Made-Up-1-1"'},
                ],
                "body": {"data": _b64url(b"hello")},
            }],
        }
        html_body, _ = GmailClient._extract_body_from_payload(payload)
        assert html_body == "hello"

    def test_invalid_bytes_replaced_not_dropped(self):
        # Single stray 0xFF byte is not valid UTF-8. With errors="replace"
        # it becomes U+FFFD instead of returning None.
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/html",
                "body": {"data": _b64url(b"ok\xffdone")},
            }],
        }
        html_body, _ = GmailClient._extract_body_from_payload(payload)
        assert html_body is not None
        assert html_body.startswith("ok")
        assert html_body.endswith("done")

    def test_charset_header_case_insensitive(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "text/plain",
                "headers": [
                    {"name": "content-type", "value": 'text/plain; CHARSET=iso-8859-15'},
                ],
                "body": {"data": _b64url("euro€".encode("iso-8859-15"))},
            }],
        }
        _, text_body = GmailClient._extract_body_from_payload(payload)
        assert text_body == "euro€"


# ── fetch_attachment_binary (D-17 — error mapping) ──────────────────


class TestGmailFetchAttachmentBinary:
    def _make_http_error(self, status: int) -> Exception:
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        type(resp).status = status
        return HttpError(resp=resp, content=b'{"error":{"message":"x"}}')

    def _attachment(self):
        from core.email.email_client import AttachmentMetadata
        return AttachmentMetadata(
            provider_message_id="msg-1",
            part_id="1",
            provider_attachment_id="att-1",
            filename="img.png",
            mime_type="image/png",
            size=10,
            content_id=None,
            is_inline=False,
            position=0,
        )

    def _stub_payload_and_service(
        self,
        client,
        monkeypatch,
        *,
        raise_exc: Exception | None = None,
        data_b64: str | None = None,
    ):
        # The real fetch_attachment_binary calls _fetch_message_payload
        # first to resolve the live ``attachmentId`` for the cached
        # ``part_id``. Stub the payload to expose that mapping; then
        # stub the SDK's ``attachments.get(...).execute()`` chain.
        payload = {
            "id": "msg-1",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "partId": "1",
                        "mimeType": "image/png",
                        "filename": "img.png",
                        "headers": [
                            {"name": "Content-Disposition", "value": "attachment; filename=img.png"},
                        ],
                        "body": {"attachmentId": "att-1", "size": 10},
                    },
                ],
            },
        }
        monkeypatch.setattr(
            client, "_fetch_message_payload", lambda mid: payload["payload"],
        )
        execute = MagicMock()
        if raise_exc is not None:
            execute.side_effect = raise_exc
        else:
            execute.return_value = {"data": data_b64 or ""}
        chain = MagicMock()
        chain.execute = execute
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.attachments.return_value.get.return_value = chain
        return execute

    def test_happy_path_returns_decoded_bytes(self, client, monkeypatch):
        self._stub_payload_and_service(client, monkeypatch, data_b64="aGVsbG8=")
        binary = client.fetch_attachment_binary("msg-1", self._attachment())
        assert binary.data == b"hello"
        assert binary.size == 5
        assert binary.mime_type == "image/png"

    def test_http_404_raises_attachment_not_found(self, client, monkeypatch):
        from core.email.errors import EmailAttachmentNotFound
        self._stub_payload_and_service(
            client, monkeypatch, raise_exc=self._make_http_error(404),
        )
        with pytest.raises(EmailAttachmentNotFound):
            client.fetch_attachment_binary("msg-1", self._attachment())

    def test_http_403_raises_download_failed_with_forbidden_reason(self, client, monkeypatch):
        from core.email.errors import EmailAttachmentDownloadFailed
        self._stub_payload_and_service(
            client, monkeypatch, raise_exc=self._make_http_error(403),
        )
        with pytest.raises(EmailAttachmentDownloadFailed) as exc_info:
            client.fetch_attachment_binary("msg-1", self._attachment())
        assert exc_info.value.detail.get("reason") == "forbidden"

    def test_http_500_after_retry_exhaustion_raises_download_failed(self, client, monkeypatch):
        from core.email.errors import EmailAttachmentDownloadFailed
        self._stub_payload_and_service(
            client, monkeypatch, raise_exc=self._make_http_error(500),
        )
        with pytest.raises(EmailAttachmentDownloadFailed) as exc_info:
            client.fetch_attachment_binary("msg-1", self._attachment())
        # 5xx maps to ``unavailable`` (transient provider — caller surfaces 503).
        assert exc_info.value.detail.get("reason") == "unavailable"

    def test_unknown_exception_after_retry_does_not_escape_untyped(self, client, monkeypatch):
        # Phase 2.3 fix: a non-HttpError after retry exhaustion must be
        # translated to ``EmailAttachmentDownloadFailed(reason="unavailable")``
        # so the service layer can map it cleanly to a 503.
        from core.email.errors import EmailAttachmentDownloadFailed
        self._stub_payload_and_service(
            client, monkeypatch, raise_exc=OSError("connection reset"),
        )
        with pytest.raises(EmailAttachmentDownloadFailed) as exc_info:
            client.fetch_attachment_binary("msg-1", self._attachment())
        assert exc_info.value.detail.get("reason") == "unavailable"


# ── list_message_attachments ────────────────────────────────────────


class TestGmailListMessageAttachments:
    def test_returns_classification_tuple(self, client, monkeypatch):
        # list_message_attachments composes _fetch_message_payload + the
        # body extractor + _classify_attachments. Stubbing both internal
        # helpers proves the wrapper keeps the (attachments, cid_map)
        # tuple shape untouched (regression for the unified send refactor).
        from core.email.email_client import AttachmentMetadata
        sample = AttachmentMetadata(
            provider_message_id="msg-1",
            part_id="2",
            provider_attachment_id=None,
            filename="doc.pdf",
            mime_type="application/pdf",
            size=512,
            content_id=None,
            is_inline=False,
            position=0,
        )
        monkeypatch.setattr(
            client, "_fetch_message_payload",
            lambda mid: {"parts": [], "mimeType": "multipart/mixed"},
        )
        monkeypatch.setattr(
            client, "_classify_attachments",
            lambda payload, mid, html_body: ({"cid-x": "data:image/png;base64,YQ=="}, [sample]),
        )
        attachments, cid_map = client.list_message_attachments("msg-1")
        assert attachments == [sample]
        assert cid_map == {"cid-x": "data:image/png;base64,YQ=="}


# ── fetch_reply_context ────────────────────────────────────────────


class TestGmailFetchReplyContext:
    """Covers the single-payload parse used by GET /reply-context.

    The implementation reuses ``_fetch_message_payload`` (which already
    has error wrapping) and ``_header_value`` / ``_extract_body_from_payload``
    for parsing. We mock the underlying ``users().messages().get()``
    call so the test exercises the parsing layer in isolation.
    """

    def _build_payload(self, *, label_ids: list[str] | None = None):
        # Mirrors the real ``messages.get(format=full)`` shape: Gmail
        # exposes ``threadId`` / ``labelIds`` at the resource ROOT
        # (not inside ``payload``). ``fetch_reply_context`` reads
        # those root-level fields via ``_fetch_message_resource``.
        return {
            "id": "msg-1",
            "threadId": "thread-1",
            "labelIds": label_ids or ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Ana Lopez <ana@example.com>"},
                    {"name": "Reply-To", "value": "editor@list.com"},
                    {"name": "To", "value": "me@me.com, carol@x.com"},
                    {"name": "Cc", "value": "dan@y.com"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Message-ID", "value": "<orig@x>"},
                    {"name": "References", "value": "<older@x>"},
                    {"name": "Date", "value": "Sat, 23 May 2026 14:32:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(b"body text").decode("ascii").rstrip("="),
                },
            },
        }

    def test_parses_full_payload_into_reply_context(self, client: GmailClient):
        payload = self._build_payload()
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = payload

        result = client.fetch_reply_context("msg-1")
        assert result.provider_message_id == "msg-1"
        assert result.thread_id == "thread-1"
        assert result.from_email == "ana@example.com"
        assert result.from_name == "Ana Lopez"
        assert result.reply_to == ["editor@list.com"]
        assert result.to_recipients == ["me@me.com", "carol@x.com"]
        assert result.cc_recipients == ["dan@y.com"]
        assert result.subject == "Hello"
        # The angle brackets are stripped from message_id at the parser boundary.
        assert result.message_id == "orig@x"
        assert result.references == "<older@x>"
        # Date header parsed by parsedate_to_datetime.
        assert result.received_at.year == 2026
        assert result.received_at.month == 5

    def test_no_reply_to_returns_empty_list(self, client: GmailClient):
        # Most messages don't carry Reply-To — the field is optional.
        payload = self._build_payload()
        payload["payload"]["headers"] = [
            h for h in payload["payload"]["headers"] if h["name"] != "Reply-To"
        ]
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = payload
        result = client.fetch_reply_context("msg-1")
        assert result.reply_to == []

    def test_no_message_id_collapses_to_empty(self, client: GmailClient):
        # Corrupt / missing Message-ID is tolerated — service guard later.
        payload = self._build_payload()
        payload["payload"]["headers"] = [
            h for h in payload["payload"]["headers"] if h["name"] != "Message-ID"
        ]
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = payload
        result = client.fetch_reply_context("msg-1")
        assert result.message_id == ""

    def test_box_derived_from_labels(self, client: GmailClient):
        # The box is computed from the labelIds — SPAM / TRASH detected.
        payload = self._build_payload(label_ids=["SPAM"])
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = payload
        result = client.fetch_reply_context("msg-1")
        assert result.box == "SPAM"

    def test_box_sent_when_label_present(self, client: GmailClient):
        payload = self._build_payload(label_ids=["SENT"])
        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = payload
        result = client.fetch_reply_context("msg-1")
        assert result.box == "SENT"

    def test_provider_failure_wrapped_as_reply_context_error(self, client: GmailClient):
        # HttpError on the underlying messages.get → translated to
        # EmailReplyContextFetchError (not the generic EmailExternalAPIError).
        from googleapiclient.errors import HttpError
        from core.email.errors import EmailReplyContextFetchError

        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        http_err = HttpError(resp=resp, content=b"missing")

        client.service = MagicMock()
        client.service.users.return_value.messages.return_value.get.return_value.execute.side_effect = http_err

        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            client.fetch_reply_context("msg-1")
        assert exc_info.value.detail.get("reason") == "provider_fetch_failed"

    def test_unauthenticated_wraps_into_reply_context_error(self, client: GmailClient):
        # ``_fetch_message_payload`` raises EmailNotAuthenticatedError (a
        # CoreError); the outer ``fetch_reply_context`` re-wraps every
        # CoreError as EmailReplyContextFetchError so the API layer
        # maps it uniformly to 502.
        from core.email.errors import EmailReplyContextFetchError
        client.service = None
        with pytest.raises(EmailReplyContextFetchError):
            client.fetch_reply_context("msg-1")


class TestClassifyAttachments:
    """D-13 strict inline-vs-attachment rule (M14).

    Exercises the real classifier with synthetic MIME trees instead of
    stubbing it, so the four-way decision (inline+referenced, inline+
    unreferenced, non-image with Content-ID, inline without bytes) is
    actually covered. ``data="WA"`` is base64url for b"X"; the classifier
    appends ``"=="`` before decoding, which pads it back to a valid value.
    """

    @staticmethod
    def _part(
        *, mime_type, filename="", cid=None, disposition=None,
        content_type_header=None, data="WA", size=1, part_id="1",
    ):
        headers = []
        if cid is not None:
            headers.append({"name": "Content-ID", "value": f"<{cid}>"})
        if disposition is not None:
            headers.append({"name": "Content-Disposition", "value": disposition})
        if content_type_header is not None:
            # B-NAME-GMAIL: a raw ``Content-Type: …; name="x"`` header lets
            # the classifier recover a filename when ``MessagePart.filename``
            # is empty. The ``mimeType`` field stays the structural type.
            headers.append({"name": "Content-Type", "value": content_type_header})
        body: dict = {"size": size}
        if data is not None:
            body["data"] = data
        return {
            "mimeType": mime_type,
            "filename": filename,
            "headers": headers,
            "body": body,
            "partId": part_id,
        }

    def test_inline_image_referenced_goes_to_cid_map(self, client: GmailClient):
        payload = {"parts": [self._part(
            mime_type="image/png", filename="logo.png", cid="logo123",
            disposition="inline",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", '<img src="cid:logo123">',
        )
        assert "logo123" in cid_map
        assert cid_map["logo123"].startswith("data:image/png;base64,")
        assert attachments == []

    def test_inline_image_with_case_mismatched_cid_still_matches(self, client: GmailClient):
        # Header ``Content-ID: <Logo123>`` vs HTML ``cid:logo123`` — the match
        # runs on the ``normalize_cid`` form, so the image stays inline instead
        # of being promoted to a downloadable with a broken icon in the body.
        payload = {"parts": [self._part(
            mime_type="image/png", filename="logo.png", cid="Logo123",
            disposition="inline",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", '<img src="cid:logo123">',
        )
        assert "logo123" in cid_map
        assert attachments == []

    def test_inline_marked_unreferenced_promoted_to_downloadable(self, client: GmailClient):
        payload = {"parts": [self._part(
            mime_type="image/png", filename="orphan.png", cid="orphan",
            disposition="inline",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", "<p>no inline reference here</p>",
        )
        assert cid_map == {}
        assert len(attachments) == 1
        assert attachments[0].is_inline is True
        assert attachments[0].content_id == "orphan"

    def test_pdf_with_content_id_is_downloadable_never_embedded(self, client: GmailClient):
        # A PDF carrying a Content-ID (but not an inline image) must surface
        # as a downloadable attachment, never embedded into the HTML.
        payload = {"parts": [self._part(
            mime_type="application/pdf", filename="invoice.pdf", cid="pdfcid",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", '<img src="cid:pdfcid">',
        )
        assert "pdfcid" not in cid_map
        assert len(attachments) == 1
        assert attachments[0].filename == "invoice.pdf"
        assert attachments[0].mime_type == "application/pdf"

    def test_inline_image_without_bytes_is_skipped(self, client: GmailClient):
        # Referenced inline image with no inline data and no attachmentId:
        # _populate_cid_map soft-fails, the part is consumed by the inline
        # branch, so it appears in neither cid_map nor attachments.
        payload = {"parts": [self._part(
            mime_type="image/png", filename="empty.png", cid="nobytes",
            disposition="inline", data=None, size=0,
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", '<img src="cid:nobytes">',
        )
        assert cid_map == {}
        assert attachments == []

    def test_empty_filename_recovered_from_content_type_name(self, client: GmailClient):
        # B-NAME-GMAIL: ``MessagePart.filename`` empty but the name lives in
        # ``Content-Type: …; name="doc.pdf"`` — recover it instead of
        # falling back to the synthetic ``attachment`` name.
        payload = {"parts": [self._part(
            mime_type="application/pdf", filename="",
            content_type_header='application/pdf; name="doc.pdf"',
            disposition="attachment",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", "<p>body</p>",
        )
        assert cid_map == {}
        assert len(attachments) == 1
        assert attachments[0].filename == "doc.pdf"
        assert attachments[0].mime_type == "application/pdf"

    def test_empty_filename_recovered_from_content_disposition_filename(self, client: GmailClient):
        # B-NAME-GMAIL: recovery from ``Content-Disposition: …; filename=``.
        payload = {"parts": [self._part(
            mime_type="application/pdf", filename="",
            disposition='attachment; filename="invoice.pdf"',
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", "<p>body</p>",
        )
        assert cid_map == {}
        assert len(attachments) == 1
        assert attachments[0].filename == "invoice.pdf"

    def test_generic_declared_type_overridden_by_extension(self, client: GmailClient):
        # B-MIME: an ``application/octet-stream`` part named ``factura.xlsx``
        # gets its type inferred from the extension. ``.xlsx`` is registered
        # explicitly by the helper (``mimetypes.add_type``), so unlike
        # ``.zip`` it resolves identically across host/container platforms.
        payload = {"parts": [self._part(
            mime_type="application/octet-stream", filename="factura.xlsx",
            disposition="attachment",
        )]}
        cid_map, attachments = client._classify_attachments(
            payload, "msg-1", "<p>body</p>",
        )
        assert len(attachments) == 1
        assert attachments[0].filename == "factura.xlsx"
        assert attachments[0].mime_type == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


# ── fetch_conversation ──────────────────────────────────────────────


class TestFetchConversation:
    """Gmail conversation viewer — threads.get(format=metadata) → members."""

    @staticmethod
    def _thread_message(
        msg_id: str,
        *,
        internal_date: str,
        labels: list[str],
        thread_id: str = "thread-1",
        from_value: str = "Alice <alice@example.com>",
        subject: str = "Hello",
    ) -> dict:
        return {
            "id": msg_id,
            "threadId": thread_id,
            "internalDate": internal_date,
            "labelIds": labels,
            "payload": {
                "headers": [
                    {"name": "From", "value": from_value},
                    {"name": "Subject", "value": subject},
                ],
            },
        }

    def test_not_authenticated_raises(self, client: GmailClient):
        client.service = None
        with pytest.raises(EmailNotAuthenticatedError):
            client.fetch_conversation("thread-1")

    def test_parses_members_with_state_and_favorite(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = {
            "messages": [
                self._thread_message(
                    "m1", internal_date="1700000000000",
                    labels=["INBOX", "STARRED"],
                ),
                self._thread_message(
                    "m2", internal_date="1700000100000",
                    labels=["SENT", "UNREAD"],
                ),
            ],
        }
        client.service = mock_service
        members = client.fetch_conversation("thread-1")

        assert len(members) == 2
        by_id = {m.provider_message_id: m for m in members}
        # box / is_read / to_* come from the SAME parser as sync; favourite is
        # derived from the STARRED label (not carried by EmailMetadata).
        assert by_id["m1"].box == "ALL_MAIL"
        assert by_id["m1"].is_read is True
        assert by_id["m1"].is_favorite is True
        assert by_id["m2"].box == "SENT"
        assert by_id["m2"].is_read is False
        assert by_id["m2"].is_favorite is False
        assert by_id["m1"].thread_id == "thread-1"
        # account_id is left unstamped for the service layer.
        assert by_id["m1"].account_id == ""

    def test_includes_trash_and_spam_members(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = {
            "messages": [
                self._thread_message("m1", internal_date="1700000000000", labels=["INBOX"]),
                self._thread_message("m-trash", internal_date="1700000100000", labels=["TRASH"]),
                self._thread_message("m-spam", internal_date="1700000200000", labels=["SPAM"]),
            ],
        }
        client.service = mock_service
        boxes = {m.provider_message_id: m.box for m in client.fetch_conversation("thread-1")}
        assert boxes == {"m1": "ALL_MAIL", "m-trash": "TRASH", "m-spam": "SPAM"}

    def test_orders_ascending_by_internal_date(self, client: GmailClient):
        mock_service = MagicMock()
        # Provider returns out of order; the client must sort oldest-first.
        mock_service.users().threads().get().execute.return_value = {
            "messages": [
                self._thread_message("newest", internal_date="1700000200000", labels=["INBOX"]),
                self._thread_message("oldest", internal_date="1700000000000", labels=["INBOX"]),
                self._thread_message("middle", internal_date="1700000100000", labels=["INBOX"]),
            ],
        }
        client.service = mock_service
        ids = [m.provider_message_id for m in client.fetch_conversation("thread-1")]
        assert ids == ["oldest", "middle", "newest"]

    def test_skips_unparseable_message_without_aborting(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = {
            "messages": [
                self._thread_message("ok", internal_date="1700000000000", labels=["INBOX"]),
                # ``labelIds`` as a non-iterable blows up label resolution; the
                # per-message try/except must skip it, not abort the thread.
                {"id": "bad", "threadId": "thread-1", "internalDate": "1700000100000",
                 "labelIds": 123, "payload": {"headers": []}},
            ],
        }
        client.service = mock_service
        ids = [m.provider_message_id for m in client.fetch_conversation("thread-1")]
        assert ids == ["ok"]

    def test_http_error_raises_external_api_error(self, client: GmailClient):
        from googleapiclient.errors import HttpError
        mock_service = MagicMock()
        resp = MagicMock()
        type(resp).status = 404
        mock_service.users().threads().get().execute.side_effect = HttpError(
            resp=resp, content=b"not found",
        )
        client.service = mock_service
        # A deleted thread (404) surfaces as a generic ExternalAPIError — the
        # service translates it to 502 (no artificial single-message fallback).
        with pytest.raises(EmailExternalAPIError, match="fetch thread"):
            client.fetch_conversation("thread-gone")

    def test_generic_exception_raises_external_api_error(self, client: GmailClient):
        mock_service = MagicMock()
        mock_service.users().threads().get().execute.side_effect = RuntimeError("boom")
        client.service = mock_service
        with pytest.raises(EmailExternalAPIError, match="RuntimeError"):
            client.fetch_conversation("thread-1")
