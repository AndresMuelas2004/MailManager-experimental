"""Tests espejo de ``core.email.helpers.construccion_mime`` (mensaje RFC 5322 saliente)."""

from __future__ import annotations

from email import message_from_bytes, policy

import pytest

from core.email.helpers import build_mime_with_attachments


# ── build_mime_with_attachments ────────────────────────────────────


class TestBuildMimeWithAttachments:

    def test_html_body_no_attachments_is_multipart_alternative(self):
        # The body is HTML now: with no attachments the root is a
        # multipart/alternative carrying a derived text/plain leg FIRST and
        # the text/html leg SECOND (the order clients require to prefer the
        # richest representation they understand).
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="hi",
            body="<p>hola</p>",
            attachments=[],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        assert msg["To"] == "to@example.com"
        assert msg["Subject"] == "hi"
        assert msg.get_content_type() == "multipart/alternative"
        subtypes = [p.get_content_type() for p in msg.iter_parts()]
        assert "text/plain" in subtypes
        assert "text/html" in subtypes
        # text/plain comes first (richest-last ordering).
        assert subtypes.index("text/plain") < subtypes.index("text/html")

    def test_html_part_carries_the_html_body(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="hi",
            body="<p>hola <strong>mundo</strong></p>",
            attachments=[],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        html_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
        assert "<strong>mundo</strong>" in html_part.get_content()

    def test_includes_attachment_with_correct_metadata(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=["cc@example.com"],
            bcc_recipients=["bcc@example.com"],
            subject="subject",
            body="body",
            attachments=[
                {
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                    "data": b"%PDF-1.4 fake",
                },
            ],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        assert msg["To"] == "to@example.com"
        assert msg["Cc"] == "cc@example.com"
        assert msg["Bcc"] == "bcc@example.com"
        # With attachments the root is multipart/mixed; its FIRST child is the
        # multipart/alternative body, followed by the attachment part(s).
        assert msg.get_content_type() == "multipart/mixed"
        children = list(msg.iter_parts())
        assert children[0].get_content_type() == "multipart/alternative"
        attachment_parts = [p for p in msg.iter_parts() if p.get_filename()]
        assert len(attachment_parts) == 1
        att = attachment_parts[0]
        assert att.get_filename() == "report.pdf"
        assert att.get_content_type() == "application/pdf"

    def test_unknown_mime_type_falls_back_to_octet_stream(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="s",
            body="b",
            attachments=[
                {"filename": "blob.bin", "mime_type": None, "data": b"\x00\x01\x02"},
            ],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        att = next(p for p in msg.iter_parts() if p.get_filename())
        assert att.get_content_type() == "application/octet-stream"

    def test_non_bytes_data_raises_email_attachment_send_failed(self):
        # Per CLAUDE.md §3 rule 3 the helper must not leak a bare TypeError
        # outside the core layer; the typed CoreError subclass carries a
        # ``reason`` field that ``translate_core_error`` routes correctly.
        from core.email.errors import EmailAttachmentSendFailed
        with pytest.raises(EmailAttachmentSendFailed) as excinfo:
            build_mime_with_attachments(
                to_recipients=["to@example.com"],
                cc_recipients=[],
                bcc_recipients=[],
                subject="s",
                body="b",
                attachments=[
                    {"filename": "x.txt", "mime_type": "text/plain", "data": "string"},
                ],
            )
        assert (excinfo.value.detail or {}).get("reason") == "invalid_attachment_data"

    def test_body_charset_is_utf8(self):
        # Both legs of the multipart/alternative must declare UTF-8 so accents
        # round-trip end-to-end through Gmail/Outlook.
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="s",
            body="<p>Atención: tildes</p>",
            attachments=[],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        charsets = {p.get_content_charset() for p in msg.iter_parts()}
        assert charsets == {"utf-8"}

    def test_utf8_filename_round_trips(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="s",
            body="b",
            attachments=[
                {"filename": "Información.pdf", "mime_type": "application/pdf", "data": b"x"},
            ],
        )
        msg = message_from_bytes(raw, policy=policy.default)
        att = next(p for p in msg.iter_parts() if p.get_filename())
        assert att.get_filename() == "Información.pdf"


# ── build_mime_with_attachments — extra_headers plumbing ───────────


class TestBuildMimeExtraHeaders:
    """The ``extra_headers`` kwarg is the Reply path's plumbing for
    injecting ``In-Reply-To`` and ``References`` into the Gmail MIME."""

    def test_extra_headers_injected_into_mime(self):
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>", "References": "<orig@x>"},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in text
        assert "References: <orig@x>" in text

    def test_none_value_skipped_silently(self):
        # Soft fallback: a caller passing partial headers (one set, one
        # ``None``) does not produce a malformed empty header.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>", "References": None},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To: <orig@x>" in text
        # ``References`` was None → silently dropped, no blank header.
        assert "References:" not in text

    def test_empty_string_value_skipped(self):
        # Whitespace-only values collapse to "no header" like ``None``.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "   ", "References": ""},
        )
        text = raw.decode("utf-8", errors="replace")
        assert "In-Reply-To:" not in text
        assert "References:" not in text

    def test_no_extra_headers_preserves_original_shape(self):
        # The default path (no extra headers) still produces a valid MIME.
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Hello",
            body="body",
            attachments=[],
            extra_headers=None,
        )
        text = raw.decode("utf-8", errors="replace")
        # Reply headers absent in a non-reply MIME.
        assert "In-Reply-To" not in text
        assert "References" not in text

    def test_dict_key_uniqueness_prevents_duplicate_headers(self):
        # Single dict → at most one occurrence per header name in the MIME
        # (the implementation's main guard against duplicate header bugs).
        raw = build_mime_with_attachments(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hello",
            body="body",
            attachments=[],
            extra_headers={"In-Reply-To": "<orig@x>"},
        )
        text = raw.decode("utf-8", errors="replace")
        assert text.count("In-Reply-To:") == 1
