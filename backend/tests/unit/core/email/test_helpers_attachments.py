"""
Unit tests for the attachment-related helpers added in `core.email.helpers`:

- ``sanitize_filename`` (D-20)
- ``format_content_disposition`` (D-21)
- ``pick_gmail_send_strategy`` / ``pick_outlook_attachment_strategy`` (D-18)
- ``build_mime_with_attachments`` (HTML multipart/alternative + Gmail send path)
- ``find_referenced_cids`` (D-13 strict inline-vs-attachment classification)
- ``retry_with_backoff`` (D-16)
- ``resolve_attachment_mime_type`` (B-MIME)
- ``extract_filename_from_headers`` (B-NAME-GMAIL)
"""

from __future__ import annotations

import urllib.parse
from email import message_from_bytes, policy

import pytest

# Every helper under test is re-exported from the package facade, so they are
# imported from ``core.email`` (the public surface the clients consume) rather
# than the internal ``core.email.helpers`` submodule (core/CLAUDE.md §4).
from core.email import (
    GmailSendStrategy,
    OutlookAttachmentStrategy,
    build_mime_with_attachments,
    extract_filename_from_headers,
    find_referenced_cids,
    format_content_disposition,
    pick_gmail_send_strategy,
    pick_outlook_attachment_strategy,
    resolve_attachment_mime_type,
    retry_with_backoff,
    sanitize_filename,
)


# ── sanitize_filename ───────────────────────────────────────────────


class TestSanitizeFilename:

    def test_clean_name_passes_through(self):
        assert sanitize_filename("report.pdf") == "report.pdf"

    def test_replaces_path_separators(self):
        assert sanitize_filename("foo/bar.pdf") == "foo-bar.pdf"
        assert sanitize_filename("foo\\bar.pdf") == "foo-bar.pdf"

    def test_replaces_windows_reserved_chars(self):
        assert sanitize_filename('a:b?c*d<e>f|g"h.pdf') == "a-b-c-d-e-f-g-h.pdf"

    def test_neutralises_path_traversal(self):
        # ``..`` becomes ``-`` to neutralise traversal attempts (D-20).
        assert ".." not in sanitize_filename("../../etc/passwd")

    def test_replaces_control_chars(self):
        assert sanitize_filename("a\x00b\x1fc.pdf") == "a-b-c.pdf"

    def test_preserves_utf8_accents(self):
        # D-20: legitimate UTF-8 must round-trip; slugifying would mangle
        # legitimate non-ASCII filenames.
        assert sanitize_filename("Información.pdf") == "Información.pdf"

    def test_preserves_extension(self):
        assert sanitize_filename("foo:bar.txt") == "foo-bar.txt"

    @pytest.mark.parametrize("reserved", [
        "CON.pdf", "PRN.txt", "AUX.log", "NUL.bin",
        "COM1.dat", "COM9.dat", "LPT1.dat", "LPT9.dat",
    ])
    def test_prefixes_windows_reserved_names(self, reserved):
        result = sanitize_filename(reserved)
        assert result.startswith("_"), f"reserved name not prefixed: {result}"

    def test_reserved_check_is_case_insensitive(self):
        assert sanitize_filename("con.pdf").startswith("_")
        assert sanitize_filename("Con.pdf").startswith("_")

    def test_collision_resolution_appends_number(self):
        result = sanitize_filename("report.pdf", existing=["report.pdf"])
        assert result == "report (1).pdf"

    def test_collision_resolution_increments(self):
        result = sanitize_filename(
            "report.pdf",
            existing=["report.pdf", "report (1).pdf", "report (2).pdf"],
        )
        assert result == "report (3).pdf"

    def test_collision_resolution_keeps_extension(self):
        result = sanitize_filename("Foo.tar.gz", existing=["Foo.tar.gz"])
        # Only the last extension is kept on collision; stem is everything before.
        assert result.endswith(".gz")
        assert " (1)" in result

    def test_empty_input_returns_default(self):
        assert sanitize_filename("") == "attachment"

    def test_whitespace_only_input_falls_back_to_default(self):
        assert sanitize_filename("   ") == "attachment"


# ── format_content_disposition ─────────────────────────────────────


class TestFormatContentDisposition:

    def test_ascii_filename_emits_both_forms(self):
        header = format_content_disposition("report.pdf")
        assert 'attachment; filename="report.pdf"' in header
        assert "filename*=UTF-8''report.pdf" in header

    def test_utf8_filename_percent_encoded(self):
        header = format_content_disposition("Información.pdf")
        encoded = urllib.parse.quote("Información.pdf", safe="")
        assert f"filename*=UTF-8''{encoded}" in header

    def test_utf8_ascii_fallback_replaces_non_ascii(self):
        header = format_content_disposition("Información.pdf")
        # The legacy ASCII fallback must not contain the non-ASCII code points.
        ascii_part = header.split(";")[1].strip()  # `filename="…"`
        assert "ó" not in ascii_part

    def test_empty_filename_falls_back_to_default(self):
        header = format_content_disposition("")
        assert 'filename="attachment"' in header
        assert "filename*=UTF-8''attachment" in header

    def test_filename_with_double_quote_is_neutralised(self):
        # A ``"`` inside the name must not break the quoted-string form —
        # only the two delimiting quotes may survive in the ASCII fallback.
        header = format_content_disposition('a"b.pdf')
        ascii_part = header.split(";")[1].strip()  # `filename="…"`
        assert ascii_part.count('"') == 2
        assert 'filename="a_b.pdf"' in header
        # The real name still rides on the RFC 5987 form.
        assert "filename*=UTF-8''" + urllib.parse.quote('a"b.pdf', safe="") in header


# ── pick_gmail_send_strategy ───────────────────────────────────────


class TestPickGmailSendStrategy:

    def test_under_5mb_simple(self):
        assert pick_gmail_send_strategy(0) is GmailSendStrategy.SIMPLE
        assert pick_gmail_send_strategy(1) is GmailSendStrategy.SIMPLE
        assert pick_gmail_send_strategy(5 * 1024 * 1024) is GmailSendStrategy.SIMPLE

    def test_over_5mb_resumable(self):
        assert pick_gmail_send_strategy(5 * 1024 * 1024 + 1) is GmailSendStrategy.RESUMABLE
        assert pick_gmail_send_strategy(50 * 1024 * 1024) is GmailSendStrategy.RESUMABLE


# ── pick_outlook_attachment_strategy ───────────────────────────────


class TestPickOutlookAttachmentStrategy:

    def test_under_3mb_simple(self):
        assert pick_outlook_attachment_strategy(0) is OutlookAttachmentStrategy.SIMPLE
        assert pick_outlook_attachment_strategy(3 * 1024 * 1024 - 1) is OutlookAttachmentStrategy.SIMPLE

    def test_at_or_over_3mb_session(self):
        # Boundary: the simple endpoint rejects payloads >= 3 MB so the
        # cut-off must be inclusive on the upload-session side.
        assert pick_outlook_attachment_strategy(3 * 1024 * 1024) is OutlookAttachmentStrategy.UPLOAD_SESSION
        assert pick_outlook_attachment_strategy(10 * 1024 * 1024) is OutlookAttachmentStrategy.UPLOAD_SESSION


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


# ── find_referenced_cids ───────────────────────────────────────────


class TestFindReferencedCids:

    def test_extracts_from_src_attribute(self):
        html = '<img src="cid:logo@x"><img src="cid:hero@y">'
        assert find_referenced_cids(html) == {"logo@x", "hero@y"}

    def test_extracts_from_background_attribute(self):
        html = '<td background="cid:bg@x">x</td>'
        assert "bg@x" in find_referenced_cids(html)

    def test_extracts_from_url_func_in_style(self):
        html = '<div style="background-image:url(cid:bg@x)">x</div>'
        assert "bg@x" in find_referenced_cids(html)

    def test_handles_angle_brackets_around_cid(self):
        html = '<img src="cid:<logo@x>">'
        assert "logo@x" in find_referenced_cids(html)

    def test_empty_html_returns_empty_set(self):
        assert find_referenced_cids("") == set()
        assert find_referenced_cids(None) == set()

    def test_ignores_non_cid_urls(self):
        html = '<img src="https://example.com/logo.png">'
        assert find_referenced_cids(html) == set()

    def test_same_cid_referenced_twice_collapses(self):
        # The same CID referenced via both ``src`` and a CSS ``url(...)``
        # collapses to a single set entry (D-13 classification dedup).
        html = '<img src="cid:x@y"><div style="background:url(cid:x@y)">z</div>'
        assert find_referenced_cids(html) == {"x@y"}


# ── retry_with_backoff ─────────────────────────────────────────────


class TestRetryWithBackoff:

    def test_returns_value_on_first_success(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        result = retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 1

    def test_retries_retryable_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("network noise")
            return "ok"

        result = retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 3

    def test_propagates_non_retryable(self):
        def fn():
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            retry_with_backoff(fn, attempts=3, sleep=lambda _s: None)

    def test_raises_last_exc_after_exhausted_attempts(self):
        def fn():
            raise OSError("always fails")

        with pytest.raises(OSError):
            retry_with_backoff(fn, attempts=2, sleep=lambda _s: None)

    def test_zero_attempts_raises_value_error(self):
        with pytest.raises(ValueError):
            retry_with_backoff(lambda: "ok", attempts=0, sleep=lambda _s: None)

    def test_retry_after_extractor_overrides_default_delay(self):
        sleeps: list[float] = []

        def fn():
            raise OSError("retry")

        with pytest.raises(OSError):
            retry_with_backoff(
                fn,
                attempts=3,
                delays=(1.0, 2.0, 4.0),
                retry_after_extractor=lambda _exc: 7.5,
                sleep=lambda s: sleeps.append(s),
            )
        # All recorded sleeps must reflect the override, not the defaults.
        assert all(s == 7.5 for s in sleeps), sleeps
        assert len(sleeps) == 2  # attempts - 1 sleeps before final raise

    def test_custom_is_retryable_predicate(self):
        def fn():
            raise RuntimeError("custom")

        with pytest.raises(RuntimeError):
            retry_with_backoff(
                fn,
                attempts=2,
                is_retryable=lambda exc: isinstance(exc, RuntimeError),
                sleep=lambda _s: None,
            )


# ── resolve_attachment_mime_type (B-MIME) ──────────────────────────


class TestResolveAttachmentMimeType:

    def test_specific_declared_type_kept_verbatim(self):
        # A specific declared type wins over any extension guess.
        assert (
            resolve_attachment_mime_type("photo.jpg", "application/pdf")
            == "application/pdf"
        )

    def test_specific_declared_type_case_is_preserved(self):
        # B-MIME / B-OUTLOOK-LOWER: the historical forced ``.lower()`` is
        # gone — a specific declared type round-trips with its case.
        assert (
            resolve_attachment_mime_type("logo.png", "image/PNG") == "image/PNG"
        )

    def test_generic_octet_stream_overridden_by_xlsx_extension(self):
        # The star case of the fix: a generic declared type but a
        # recognisable Office extension resolves to the real Office type.
        assert (
            resolve_attachment_mime_type("factura.xlsx", "application/octet-stream")
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_generic_octet_stream_overridden_by_docx_extension(self):
        assert (
            resolve_attachment_mime_type("notas.docx", "application/octet-stream")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_generic_octet_stream_overridden_by_pptx_extension(self):
        assert (
            resolve_attachment_mime_type("slides.pptx", "application/octet-stream")
            == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    def test_empty_declared_type_falls_back_to_extension(self):
        assert resolve_attachment_mime_type("doc.pdf", "") == "application/pdf"

    def test_none_declared_type_falls_back_to_extension(self):
        assert resolve_attachment_mime_type("doc.pdf", None) == "application/pdf"

    def test_generic_type_with_unknown_extension_stays_octet_stream(self):
        assert (
            resolve_attachment_mime_type("blob.unknownext", "application/octet-stream")
            == "application/octet-stream"
        )

    def test_no_extension_and_generic_type_stays_octet_stream(self):
        assert (
            resolve_attachment_mime_type("noextension", "application/octet-stream")
            == "application/octet-stream"
        )

    def test_no_filename_and_generic_type_stays_octet_stream(self):
        assert (
            resolve_attachment_mime_type(None, "application/octet-stream")
            == "application/octet-stream"
        )

    @pytest.mark.parametrize("declared", [
        "application/octet-stream",
        "application/binary",
        "binary/octet-stream",
        "APPLICATION/OCTET-STREAM",  # case-insensitive generic match
    ])
    def test_all_generic_synonyms_yield_to_the_extension(self, declared):
        assert (
            resolve_attachment_mime_type("sheet.xlsx", declared)
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    @pytest.mark.parametrize("filename,expected", [
        ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("a.rar", "application/vnd.rar"),
        ("a.7z", "application/x-7z-compressed"),
        ("a.webp", "image/webp"),
    ])
    def test_add_type_registration_anti_regression(self, filename, expected):
        # Anti-regression for ``mimetypes.add_type``: the runtime Linux
        # container's mimetypes table does NOT know these extensions
        # natively. If a base-image change ever dropped the registration,
        # the generic-declared case would silently resolve to
        # octet-stream — this test pins the registered types explicitly.
        assert resolve_attachment_mime_type(filename, "application/octet-stream") == expected


# ── extract_filename_from_headers (B-NAME-GMAIL) ───────────────────


class TestExtractFilenameFromHeaders:

    def test_recovers_from_content_disposition_filename(self):
        headers = [
            {"name": "Content-Disposition", "value": 'attachment; filename="doc.pdf"'},
        ]
        assert extract_filename_from_headers(headers) == "doc.pdf"

    def test_recovers_from_content_type_name_when_no_disposition(self):
        headers = [
            {"name": "Content-Type", "value": 'application/pdf; name="report.pdf"'},
        ]
        assert extract_filename_from_headers(headers) == "report.pdf"

    def test_content_disposition_takes_precedence_over_content_type(self):
        headers = [
            {"name": "Content-Type", "value": 'application/pdf; name="from-ct.pdf"'},
            {"name": "Content-Disposition", "value": 'attachment; filename="from-cd.pdf"'},
        ]
        assert extract_filename_from_headers(headers) == "from-cd.pdf"

    def test_decodes_rfc2231_extended_value(self):
        # filename*=utf-8''… is decoded by the legacy email.message parser.
        headers = [
            {
                "name": "Content-Disposition",
                "value": "attachment; filename*=utf-8''Informaci%C3%B3n.pdf",
            },
        ]
        assert extract_filename_from_headers(headers) == "Información.pdf"

    def test_decodes_rfc2047_encoded_word(self):
        # =?utf-8?B?…?= is decoded on top of the parser's output.
        headers = [
            {
                "name": "Content-Disposition",
                "value": 'attachment; filename="=?utf-8?B?SW5mb3JtYWNpw7NuLnBkZg==?="',
            },
        ]
        assert extract_filename_from_headers(headers) == "Información.pdf"

    def test_plain_legible_name_is_returned_unchanged(self):
        # The RFC 2047 decode is a no-op on plain text (idempotent).
        headers = [
            {"name": "Content-Disposition", "value": 'attachment; filename="plain name.txt"'},
        ]
        assert extract_filename_from_headers(headers) == "plain name.txt"

    def test_returns_none_when_no_name_present(self):
        headers = [
            {"name": "Content-Disposition", "value": "attachment"},
            {"name": "Content-Type", "value": "application/pdf"},
        ]
        assert extract_filename_from_headers(headers) is None

    def test_returns_none_for_none_headers(self):
        assert extract_filename_from_headers(None) is None

    def test_returns_none_for_empty_headers(self):
        assert extract_filename_from_headers([]) is None

    def test_ignores_header_case(self):
        # Header name matching is case-insensitive (providers vary).
        headers = [
            {"name": "content-disposition", "value": 'attachment; filename="x.zip"'},
        ]
        assert extract_filename_from_headers(headers) == "x.zip"
