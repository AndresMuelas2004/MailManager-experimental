"""Tests espejo de ``core.email.helpers.nombres_archivo`` (sanitizacion, MIME y Content-Disposition)."""

from __future__ import annotations

import urllib.parse

import pytest

from core.email.helpers import (
    extract_filename_from_headers,
    format_content_disposition,
    resolve_attachment_mime_type,
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
