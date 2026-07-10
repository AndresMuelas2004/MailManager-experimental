"""Tests espejo de ``core.email.helpers.html_texto`` (HTML <-> texto y citas del composer)."""

from __future__ import annotations

from datetime import datetime, timezone

from core.email.helpers import (
    _html_to_text,
    build_quoted_body,
    build_quoted_body_html,
    flatten_html_document,
    html_to_plain_text_alternative,
    plain_text_to_html,
)


# ── _html_to_text ───────────────────────────────────────────────────


class TestHtmlToText:
    """Covers the HTML→plain-text degrader used for the reply quote."""

    def test_empty_html_returns_empty(self):
        assert _html_to_text("") == ""
        assert _html_to_text(None) == ""

    def test_basic_text_extraction(self):
        assert "hello world" in _html_to_text("<p>hello world</p>")

    def test_script_content_discarded(self):
        out = _html_to_text("<p>visible</p><script>alert('x');</script>")
        assert "alert" not in out
        assert "visible" in out

    def test_style_content_discarded(self):
        # ``<style>`` content must never leak as visible characters.
        out = _html_to_text("<p>visible</p><style>p{color:red}</style>")
        assert "color:red" not in out
        assert "visible" in out

    def test_html_entities_decoded(self):
        # ``&amp;`` is decoded to ``&`` by the degrader.
        out = _html_to_text("a&amp;b")
        assert "&" in out
        # The literal letters around the entity survive.
        assert "a" in out
        assert "b" in out

    def test_malformed_html_does_not_crash(self):
        # Stdlib HTMLParser tolerates unbalanced tags — soft fallback.
        out = _html_to_text("<p>line<broken")
        assert "line" in out

    def test_block_tags_produce_newlines(self):
        out = _html_to_text("<p>line1</p><p>line2</p>")
        assert "line1" in out
        assert "line2" in out
        # Some line break between paragraphs.
        assert "\n" in out

    def test_truncated_to_max_chars(self):
        # A long body is clipped with a marker so the composer cannot OOM.
        long_html = "<p>" + ("a" * 100_000) + "</p>"
        out = _html_to_text(long_html, max_chars=50_000)
        assert len(out) <= 50_000 + len("\n[...truncado...]") + 1
        assert "[...truncado...]" in out


# ── build_quoted_body ──────────────────────────────────────────────


class TestBuildQuotedBody:
    """Covers the quoted-body assembly for reply / reply_all / forward."""

    _RECEIVED = datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc)

    def test_reply_uses_quoted_lines_prefix(self):
        out = build_quoted_body(
            None, "Hola\nQué tal",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        # Gmail-style "> " prefix on every line of the quote.
        assert "> Hola" in out
        assert "> Qué tal" in out
        # The header reads "El <date>, <sender> escribió:".
        assert "escribió:" in out
        assert "Ana" in out and "ana@x.com" in out

    def test_reply_all_same_shape_as_reply(self):
        # The quoted-body output for reply_all matches reply for the
        # same inputs — the cc difference is in compute_reply_recipients,
        # not here.
        out = build_quoted_body(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply_all",
        )
        assert "> Body" in out
        assert "escribió:" in out

    def test_forward_uses_mensaje_reenviado_header(self):
        out = build_quoted_body(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="forward",
            to_recipients=["alice@x"], cc_recipients=["bob@x"],
            subject="Hello",
        )
        # Forward uses a block header instead of "> " quoting.
        assert "Mensaje reenviado" in out
        assert "De: Ana <ana@x.com>" in out
        assert "Asunto: Hello" in out
        assert "Para: alice@x" in out
        assert "Cc: bob@x" in out
        # The body is NOT prefixed with "> " in forward shape.
        assert "> Body" not in out

    def test_uses_text_body_when_provided(self):
        # text_body wins over html_body (already-degraded path).
        out = build_quoted_body(
            "<p>html version</p>", "plain version",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "plain version" in out
        assert "html version" not in out

    def test_degrades_html_when_no_text(self):
        # Fall back to _html_to_text when text_body is None.
        out = build_quoted_body(
            "<p>only html</p>", None,
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "only html" in out

    def test_empty_body_still_emits_header(self):
        # No body just produces the header line.
        out = build_quoted_body(
            None, "",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "escribió:" in out


# ── plain_text_to_html ─────────────────────────────────────────────


class TestPlainTextToHtml:
    """Legacy plain-text body → minimal HTML fragment (escape then nl2br)."""

    def test_escapes_ampersand_lt_gt(self):
        # & must be escaped BEFORE < / > so their entities are not re-escaped.
        assert plain_text_to_html("a & b < c > d") == "<p>a &amp; b &lt; c &gt; d</p>"

    def test_newline_becomes_break(self):
        assert plain_text_to_html("line1\nline2") == "<p>line1<br>line2</p>"

    def test_crlf_normalised_to_single_break(self):
        # \r\n must collapse to one <br>, never <br><br>.
        assert plain_text_to_html("crlf\r\nthere") == "<p>crlf<br>there</p>"

    def test_lone_cr_becomes_break(self):
        assert plain_text_to_html("cr\rthere") == "<p>cr<br>there</p>"

    def test_wraps_in_single_paragraph(self):
        assert plain_text_to_html("hello") == "<p>hello</p>"

    def test_empty_returns_empty_string(self):
        assert plain_text_to_html("") == ""

    def test_whitespace_only_returns_empty_string(self):
        assert plain_text_to_html("   ") == ""

    def test_none_returns_empty_string(self):
        assert plain_text_to_html(None) == ""


# ── flatten_html_document ──────────────────────────────────────────


class TestFlattenHtmlDocument:
    """Strip <html>/<head>/<body> wrappers + head-only subtrees (Outlook round-trip)."""

    def test_unwraps_body_fragment_from_full_document(self):
        html = (
            "<html><head><meta charset=us-ascii><title>T</title></head>"
            "<body><p>x</p></body></html>"
        )
        assert flatten_html_document(html) == "<p>x</p>"

    def test_fragment_without_wrapper_returned_intact(self):
        assert flatten_html_document("<p>already fragment</p>") == "<p>already fragment</p>"

    def test_discards_script_style_subtrees(self):
        html = "<html><body><div>d</div><script>bad()</script><style>p{}</style></body></html>"
        result = flatten_html_document(html)
        assert "<div>d</div>" in result
        assert "bad()" not in result
        assert "p{}" not in result
        assert "<script" not in result.lower()
        assert "<style" not in result.lower()

    def test_drops_title_and_meta_content(self):
        html = "<html><head><title>Subject Leak</title></head><body><p>body</p></body></html>"
        result = flatten_html_document(html)
        assert "Subject Leak" not in result
        assert "<p>body</p>" in result

    def test_empty_returns_empty_string(self):
        assert flatten_html_document("") == ""

    def test_none_returns_empty_string(self):
        assert flatten_html_document(None) == ""


# ── html_to_plain_text_alternative ─────────────────────────────────


class TestHtmlToPlainTextAlternative:
    """Derived text/plain leg of Gmail's multipart/alternative (enriched degrader)."""

    def test_link_text_differs_from_url(self):
        # <a href="url">text</a> → "text (url)".
        out = html_to_plain_text_alternative('<a href="http://x.com">link text</a>')
        assert out == "link text (http://x.com)"

    def test_link_text_equals_url(self):
        # When text == href, emit only the url (no redundant duplication).
        out = html_to_plain_text_alternative('<a href="http://x.com">http://x.com</a>')
        assert out == "http://x.com"

    def test_bullet_list_uses_dash_marker(self):
        out = html_to_plain_text_alternative("<ul><li>one</li><li>two</li></ul>")
        assert "- one" in out
        assert "- two" in out

    def test_ordered_list_uses_incrementing_counter(self):
        out = html_to_plain_text_alternative("<ol><li>first</li><li>second</li></ol>")
        assert "1. first" in out
        assert "2. second" in out

    def test_blockquote_lines_prefixed_with_gt(self):
        # Every line inside a <blockquote> gets the RFC 3676 "> " prefix.
        out = html_to_plain_text_alternative("<blockquote>line1<br>line2</blockquote>")
        assert "> line1" in out
        assert "> line2" in out

    def test_nested_blockquote_doubles_the_prefix(self):
        out = html_to_plain_text_alternative(
            "<blockquote><p>outer</p><blockquote><p>inner</p></blockquote></blockquote>"
        )
        assert "> outer" in out
        assert "> > inner" in out

    def test_collapses_runs_of_blank_lines_to_two(self):
        out = html_to_plain_text_alternative("<p>a</p>\n\n\n\n<p>b</p>")
        assert "\n\n\n" not in out

    def test_truncates_without_marker(self):
        # ~1 MB cap is SILENT — this is the body the recipient reads, not a
        # quote, so there is no "[...truncado...]" marker (unlike _html_to_text).
        big = "<p>" + ("x" * 2_000_000) + "</p>"
        out = html_to_plain_text_alternative(big)
        assert len(out) <= 1_000_000
        assert "[...truncado...]" not in out

    def test_empty_returns_empty_string(self):
        assert html_to_plain_text_alternative("") == ""

    def test_none_returns_empty_string(self):
        assert html_to_plain_text_alternative(None) == ""


# ── build_quoted_body_html ─────────────────────────────────────────


class TestBuildQuotedBodyHtml:
    """HTML reply / forward quote: attribution line + <blockquote> fragment."""

    _RECEIVED = datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc)

    def test_reply_wraps_original_in_blockquote(self):
        out = build_quoted_body_html(
            None, "Hola\nQué tal",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        # The original is degraded then re-promoted inside a <blockquote>.
        assert "<blockquote" in out
        assert "Hola<br>Qué tal" in out
        # Attribution line precedes the quote.
        assert "escribió:" in out
        assert "Ana" in out

    def test_reply_escapes_sender_email_angle_brackets(self):
        # The attribution line escapes the email so an &lt;…&gt; cannot
        # smuggle markup into the seeded HTML.
        out = build_quoted_body_html(
            None, "body",
            from_name="A&B", from_email="a@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        assert "&lt;a@x.com&gt;" in out
        assert "A&amp;B" in out

    def test_reply_all_same_shape_as_reply(self):
        out = build_quoted_body_html(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply_all",
        )
        assert "<blockquote" in out
        assert "escribió:" in out

    def test_forward_uses_mensaje_reenviado_block(self):
        out = build_quoted_body_html(
            None, "Body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="forward",
            to_recipients=["alice@x"], cc_recipients=["bob@x"],
            subject="Hello",
        )
        assert "Mensaje reenviado" in out
        assert "De: Ana &lt;ana@x.com&gt;" in out
        assert "Asunto: Hello" in out
        assert "Para: alice@x" in out
        assert "Cc: bob@x" in out
        assert "<blockquote" in out

    def test_blockquote_style_within_outbound_allowlist(self):
        # The inline <blockquote> style must use only properties the
        # outbound sanitizer keeps (lockstep with _HTML_QUOTE_BLOCKQUOTE_STYLE).
        out = build_quoted_body_html(
            None, "body",
            from_name="Ana", from_email="ana@x.com",
            received_at=self._RECEIVED, action="reply",
        )
        from api.services.outbound_html_pipeline import sanitize_outbound_html
        sanitized = sanitize_outbound_html(out)
        # The quote frame survives a re-sanitisation pass (no-op on persist).
        assert "border-left" in sanitized.lower()
        assert "<blockquote" in sanitized

    def test_uses_text_body_when_provided(self):
        # text_body wins over html_body (already-degraded path).
        out = build_quoted_body_html(
            "<p>html version</p>", "plain version",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "plain version" in out
        assert "html version" not in out

    def test_degrades_html_when_no_text(self):
        out = build_quoted_body_html(
            "<p>only html</p>", None,
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "only html" in out

    def test_empty_body_still_emits_header_without_blockquote(self):
        out = build_quoted_body_html(
            None, "",
            from_name="A", from_email="a@x",
            received_at=self._RECEIVED, action="reply",
        )
        assert "escribió:" in out
        # No empty <blockquote> when there is nothing to quote.
        assert "<blockquote" not in out
