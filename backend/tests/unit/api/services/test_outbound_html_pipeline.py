"""
Unit tests for the outbound HTML sanitizer (``sanitize_outbound_html``).

Mirror of ``test_sanitize.py`` (the inbound viewer pipeline) but for the
**outbound** pipeline that cleans the rich-text composer body before it is
persisted to a draft and shipped to Gmail / Outlook. The allowlist here is
deliberately much stricter: only the small tag set TipTap can emit
(``strong``/``em``/``u``/lists/links + the reply/forward ``blockquote``)
survives; everything else (``<img>``, ``<table>``, ``<style>``, document
wrappers, inline styles on non-quote tags) is stripped with ``strip=True``
so the user's **text** is kept while the markup is dropped.

One behaviour per test (unit_guide / CLAUDE.md §2). The entry point is
imported from ``services_helpers`` (the re-export the services consume),
matching how ``test_sanitize.py`` imports the inbound alias.
"""

from __future__ import annotations

from api.services.services_helpers import sanitize_outbound_html


# ---------------------------------------------------------------------------
# Allowed tags survive
# ---------------------------------------------------------------------------


def test_preserves_strong_em_underline():
    result = sanitize_outbound_html("<strong>b</strong><em>i</em><u>u</u>")
    assert "<strong>b</strong>" in result
    assert "<em>i</em>" in result
    assert "<u>u</u>" in result


def test_preserves_bullet_and_ordered_lists():
    result = sanitize_outbound_html("<ul><li>one</li></ul><ol><li>two</li></ol>")
    assert "<ul><li>one</li></ul>" in result
    assert "<ol><li>two</li></ol>" in result


def test_preserves_paragraph_and_break():
    result = sanitize_outbound_html("<p>para</p><br>")
    assert "<p>para</p>" in result
    assert "<br>" in result


def test_preserves_blockquote_tag():
    result = sanitize_outbound_html("<blockquote>quote</blockquote>")
    assert "<blockquote>quote</blockquote>" in result


# ---------------------------------------------------------------------------
# Disallowed tags removed, text kept (strip=True)
# ---------------------------------------------------------------------------


def test_strips_heading_keeps_text():
    result = sanitize_outbound_html("<h1>Title</h1>")
    assert "<h1>" not in result
    assert "Title" in result


def test_strips_table_keeps_text():
    result = sanitize_outbound_html("<table><tr><td>cell</td></tr></table>")
    assert "<table>" not in result
    assert "<td>" not in result
    assert "cell" in result


def test_strips_img_entirely():
    result = sanitize_outbound_html('<img src="http://x/y.png">')
    assert "<img" not in result


def test_strips_div_and_span_keeps_text():
    result = sanitize_outbound_html("<div>d</div><span>s</span>")
    assert "<div>" not in result
    assert "<span>" not in result
    assert "d" in result
    assert "s" in result


def test_strips_font_tag_keeps_text():
    result = sanitize_outbound_html('<font color="red">text</font>')
    assert "<font" not in result
    assert "text" in result


def test_strips_style_block_tag():
    result = sanitize_outbound_html("<style>p{color:red}</style><p>x</p>")
    assert "<style>" not in result
    assert "<p>x</p>" in result


# ---------------------------------------------------------------------------
# Script / event handlers / dangerous protocols
# ---------------------------------------------------------------------------


def test_strips_script_tag():
    result = sanitize_outbound_html("<script>alert(1)</script><p>safe</p>")
    # The executable ``<script>`` element is removed; the surviving text is
    # inert (strip=True keeps text content, but no script can run).
    assert "<script>" not in result
    assert "<p>safe</p>" in result


def test_strips_event_handler_attribute():
    result = sanitize_outbound_html('<p onclick="alert(1)">click</p>')
    assert "onclick" not in result
    assert "click" in result


def test_blocks_javascript_href_drops_the_attribute():
    result = sanitize_outbound_html('<a href="javascript:alert(1)">evil</a>')
    assert "javascript:" not in result
    # The link text survives; only the unsafe href is dropped.
    assert "evil" in result


def test_drops_data_url_href():
    result = sanitize_outbound_html('<a href="data:text/html,foo">d</a>')
    assert "data:" not in result


def test_allows_http_href():
    result = sanitize_outbound_html('<a href="http://x.com">ok</a>')
    assert 'href="http://x.com"' in result


def test_allows_https_href():
    result = sanitize_outbound_html('<a href="https://x.com/p">ok</a>')
    assert 'href="https://x.com/p"' in result


def test_allows_mailto_href():
    result = sanitize_outbound_html('<a href="mailto:a@b.com">mail</a>')
    assert 'href="mailto:a@b.com"' in result


# ---------------------------------------------------------------------------
# Link hardening — every surviving <a> gets target + rel
# ---------------------------------------------------------------------------


def test_links_forced_to_blank_and_noopener_rel():
    result = sanitize_outbound_html('<a href="https://x.com">link</a>')
    assert 'target="_blank"' in result
    assert "noopener" in result
    assert "noreferrer" in result
    assert "nofollow" in result


def test_link_hardening_overrides_existing_target():
    result = sanitize_outbound_html('<a href="https://x.com" target="_self">link</a>')
    assert 'target="_blank"' in result
    assert 'target="_self"' not in result


# ---------------------------------------------------------------------------
# Inline style — allowed only on <blockquote>, filtered to the minimal set
# ---------------------------------------------------------------------------


def test_blockquote_style_keeps_allowed_quote_properties():
    html = (
        '<blockquote style="border-left:2px solid #ccc;margin:0;'
        'padding-left:1ex;color:#555">q</blockquote>'
    )
    result = sanitize_outbound_html(html)
    normalized = result.replace(" ", "").lower()
    assert "border-left:2pxsolid#ccc" in normalized
    assert "margin:0" in normalized
    assert "padding-left:1ex" in normalized
    assert "color:#555" in normalized


def test_blockquote_style_drops_unallowed_property():
    html = '<blockquote style="position:fixed;border-left:2px solid #ccc">q</blockquote>'
    result = sanitize_outbound_html(html)
    assert "position" not in result.lower()
    # The allowed quote property still survives.
    assert "border-left" in result.lower()


def test_style_on_paragraph_is_dropped():
    result = sanitize_outbound_html('<p style="color:red">styled</p>')
    assert "style=" not in result
    assert "<p>styled</p>" in result


# ---------------------------------------------------------------------------
# Document wrapper flattening
# ---------------------------------------------------------------------------


def test_flattens_html_head_body_wrapper():
    html = "<html><head><title>T</title></head><body><p>body</p></body></html>"
    result = sanitize_outbound_html(html)
    assert "<html>" not in result.lower()
    assert "<head>" not in result.lower()
    assert "<body>" not in result.lower()
    # The body content survives as a clean fragment.
    assert "<p>body</p>" in result


# ---------------------------------------------------------------------------
# Empty / whitespace pass-through (fail-soft)
# ---------------------------------------------------------------------------


def test_empty_string_returned_unchanged():
    assert sanitize_outbound_html("") == ""


def test_whitespace_only_returned_unchanged():
    assert sanitize_outbound_html("   ") == "   "


# ---------------------------------------------------------------------------
# Lockstep with the reply/forward quote style
# ---------------------------------------------------------------------------
# The seeded reply/forward body wraps the original in a ``<blockquote>`` whose
# inline style is ``core.email.helpers._HTML_QUOTE_BLOCKQUOTE_STYLE``. Every CSS
# property it uses MUST be in this sanitizer's ``_ALLOWED_CSS_PROPERTIES`` or
# re-sanitising the seeded body (create_draft / update_draft / send) silently
# strips the quote frame (repository_guide.md / core_guide.md). The hard-coded
# ``test_blockquote_style_keeps_allowed_quote_properties`` above checks the
# behaviour for a sample; this one checks the two CONSTANTS against each other
# so a future edit to either side fails fast instead of regressing in prod.


def test_blockquote_quote_style_is_subset_of_outbound_allowlist():
    from api.services.outbound_html_pipeline import _ALLOWED_CSS_PROPERTIES
    from core.email.helpers import _HTML_QUOTE_BLOCKQUOTE_STYLE

    quote_properties = {
        declaration.split(":", 1)[0].strip().lower()
        for declaration in _HTML_QUOTE_BLOCKQUOTE_STYLE.split(";")
        if declaration.strip()
    }
    assert quote_properties, "quote style constant unexpectedly declares no properties"
    missing = quote_properties - _ALLOWED_CSS_PROPERTIES
    assert not missing, (
        "blockquote quote style uses CSS properties absent from the outbound "
        f"sanitizer allowlist: {sorted(missing)}"
    )
