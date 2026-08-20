"""
Unit tests for the email HTML pipeline (``prepare_email_html``).

The public entry point is re-exported from ``services_helpers`` under the
legacy name ``sanitize_email_html`` so existing callers keep working. These
tests import the legacy alias to exercise the full pipeline end-to-end.
"""

from __future__ import annotations

import re

from api.services.image_proxy_signing import SENTINEL_PREFIX, verify_and_extract
from api.services.services_helpers import sanitize_email_html


# ---------------------------------------------------------------------------
# Script / event handler / dangerous protocol removal
# ---------------------------------------------------------------------------


def test_strips_script_tags():
    result = sanitize_email_html("<p>hello</p><script>alert(1)</script>")
    assert "<script>" not in result
    assert "alert(1)" not in result
    assert "<p>hello</p>" in result


def test_preserves_safe_tags():
    html = '<p>Text</p><b>Bold</b><a href="https://example.com">Link</a><img src="https://img.png" alt="img">'
    result = sanitize_email_html(html)
    assert "<p>" in result
    assert "<b>" in result
    assert "<a " in result
    assert "<img " in result


def test_strips_event_handlers():
    html = '<div onclick="alert(1)">click me</div>'
    result = sanitize_email_html(html)
    assert "onclick" not in result
    assert "click me" in result


def test_blocks_javascript_href():
    html = '<a href="javascript:alert(1)">evil</a>'
    result = sanitize_email_html(html)
    assert "javascript:" not in result


def test_allows_mailto_href():
    html = '<a href="mailto:test@example.com">mail</a>'
    result = sanitize_email_html(html)
    assert 'href="mailto:test@example.com"' in result


def test_allows_cid_img_src():
    html = '<img src="cid:image001">'
    result = sanitize_email_html(html)
    assert 'src="cid:image001"' in result


def test_allows_data_url_img_src():
    html = '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==" alt="logo">'
    result = sanitize_email_html(html)
    assert 'src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="' in result


def test_empty_string_returns_empty():
    assert sanitize_email_html("") == ""


def test_whitespace_returns_as_is():
    assert sanitize_email_html("   ") == "   "


def test_strips_onerror_on_img():
    html = '<img src="x" onerror="alert(1)">'
    result = sanitize_email_html(html)
    assert "onerror" not in result
    assert "<img " in result


# ---------------------------------------------------------------------------
# CSS inlining and <style> preservation
# ---------------------------------------------------------------------------


def test_inlines_style_rules_into_attributes():
    """Simple rules still get inlined for compatibility with strict clients."""
    html = "<html><head><style>p{color:red}</style></head><body><p>hi</p></body></html>"
    result = sanitize_email_html(html)
    # premailer inlines the rule onto the <p> tag
    assert "color:red" in result.replace(" ", "")


def test_inlines_class_selectors():
    html = (
        "<html><head><style>.btn{background:#1a73e8;color:#fff}</style></head>"
        "<body><a class=\"btn\" href=\"https://example.com\">click</a></body></html>"
    )
    result = sanitize_email_html(html)
    normalized = result.replace(" ", "").lower()
    assert "background:#1a73e8" in normalized
    assert "color:#fff" in normalized


def test_preserves_media_queries_in_style_block():
    """``@media`` queries must survive so responsive desktop layouts render."""
    html = (
        "<html><head><style>"
        ".mobile-hide{display:none}"
        "@media (min-width:600px){.mobile-hide{display:block}.desktop-only{display:block}}"
        "</style></head><body>"
        '<div class="mobile-hide">desktop content</div>'
        '<div class="desktop-only">extra</div>'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    # The <style> block is kept so the browser can apply the @media override
    # at the iframe viewport width. Without this the two divs would stay
    # display:none and the email would render empty (eDreams/Netcapital bug).
    assert "@media" in result
    assert "min-width" in result
    assert "desktop content" in result
    assert "extra" in result


def test_preserves_pseudo_class_rules():
    """Pseudo-classes (``:hover``) are not inlinable — must survive in <style>."""
    html = (
        "<html><head><style>"
        "a{color:#333}"
        "a:hover{color:#1a73e8;text-decoration:underline}"
        "</style></head><body>"
        '<a href="https://example.com">link</a>'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert ":hover" in result
    normalized = result.replace(" ", "").lower()
    assert "color:#1a73e8" in normalized


def test_style_block_strips_dangerous_expression():
    html = (
        "<html><head><style>"
        "div{width:expression(alert(1))}"
        "</style></head><body><div>hi</div></body></html>"
    )
    result = sanitize_email_html(html)
    assert "expression(" not in result
    assert "alert(1)" not in result


def test_style_block_strips_dangerous_url_scheme():
    html = (
        "<html><head><style>"
        "body{background:url(javascript:alert(1))}"
        "</style></head><body>x</body></html>"
    )
    result = sanitize_email_html(html)
    assert "javascript:" not in result


def test_style_block_strips_import_rule():
    """``@import`` would fetch remote CSS — must be dropped."""
    html = (
        "<html><head><style>"
        "@import url('https://evil.example.com/steal.css');"
        "p{color:red}"
        "</style></head><body><p>hi</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "@import" not in result
    assert "evil.example.com" not in result
    # The rest of the stylesheet still works
    assert "color:red" in result.replace(" ", "")


def test_style_block_strips_unknown_at_rules():
    """``@keyframes`` (animation) is not in the allow-list — must be dropped."""
    html = (
        "<html><head><style>"
        "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}"
        "p{color:red}"
        "</style></head><body><p>hi</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "@keyframes" not in result
    assert "rotate" not in result
    # The rest survives
    assert "color:red" in result.replace(" ", "")


def test_premailer_failure_falls_back(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated premailer failure")

    import premailer
    monkeypatch.setattr(premailer, "transform", boom)
    html = "<p>hello</p><script>alert(1)</script>"
    result = sanitize_email_html(html)
    # Falls back to bleach-only: script stripped, content preserved
    assert "<script>" not in result
    assert "<p>hello</p>" in result


def test_inlining_does_not_break_cid_img_src():
    html = (
        "<html><head><style>img{border:0}</style></head>"
        "<body><img src=\"cid:logo@x\"></body></html>"
    )
    result = sanitize_email_html(html)
    assert 'src="cid:logo@x"' in result


# ---------------------------------------------------------------------------
# MSO / IE conditional comment unwrap
# ---------------------------------------------------------------------------


def test_discards_mso_hidden_conditional_desktop_layout():
    """Outlook-only (downlevel-hidden) blocks are discarded, not unwrapped.

    The pipeline renders inside an iframe which is NOT Outlook/IE, so any
    content wrapped in ``<!--[if mso | IE]>…<![endif]-->`` is invisible to
    our client. Previously we unwrapped it, which caused visible duplication
    whenever templates shipped both an MSO-only desktop variant AND a
    non-MSO variant (Medusa Festival #20, Santander Open Academy #73).
    """
    html = (
        '<!--[if mso | IE]>'
        '<table width="600"><tr><td>DESKTOP</td></tr></table>'
        '<![endif]-->'
        '<table width="4%"><tr><td>MOBILE</td></tr></table>'
    )
    result = sanitize_email_html(html)
    # Outlook-only DESKTOP block is gone
    assert "DESKTOP" not in result
    # Non-MSO mobile placeholder stays
    assert "MOBILE" in result


def test_unwraps_non_mso_conditional():
    html = (
        "<!--[if !mso]><!-->"
        "<p>FALLBACK</p>"
        "<!--<![endif]-->"
    )
    result = sanitize_email_html(html)
    assert "FALLBACK" in result


def test_unwraps_downlevel_revealed_short_form():
    """eDreams/Outlook newsletter pattern: ``<!--[if !mso]><!-- -->…<!--<![endif]-->``.

    The short-form downlevel-revealed conditional uses nested comment markers
    (``<!-- -->`` / ``<!--``) to hide the ``[if …]`` tokens from standards-
    compliant parsers. If the regex leaves either half of those nested
    markers behind, html5lib treats the following markup as comment content
    and bleach drops big chunks of the email. This verifies both halves are
    stripped cleanly.
    """
    html = (
        '<!--[if !mso]><!-- -->'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge">'
        '<!--<![endif]-->'
        '<p>Body content must render.</p>'
    )
    result = sanitize_email_html(html)
    assert "Body content must render." in result
    # No stray unclosed comments, no leftover conditional tokens
    assert "<!--" not in result
    assert "[if" not in result
    assert "endif" not in result


def test_unwraps_mso_xml_island():
    """``<!--[if gte mso 9]><xml>…</xml><![endif]-->`` — Office XML settings.

    Common in newsletter HTML head. Must be fully stripped (or at least
    unwrapped) without leaving dangling comment markers that would confuse
    html5lib.
    """
    html = (
        '<!--[if gte mso 9]><xml>'
        '<o:OfficeDocumentSettings><o:AllowPNG/></o:OfficeDocumentSettings>'
        '</xml><![endif]-->'
        '<p>Still here.</p>'
    )
    result = sanitize_email_html(html)
    assert "Still here." in result
    assert "<!--" not in result


def test_mso_unwrap_ignores_malformed_conditional():
    # No closing <![endif]--> → the regex doesn't match; bleach then strips
    # the residual comment start. The inner tag survives intact.
    html = "<!--[if mso]><table>broken"
    result = sanitize_email_html(html)
    assert "<!--" not in result


# ---------------------------------------------------------------------------
# Charset normalisation — mojibake prevention
# ---------------------------------------------------------------------------


def test_strips_outlook_us_ascii_charset_meta():
    html = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=us-ascii"></head>'
        "<body><p>Prácticas</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "charset=us-ascii" not in result.lower()
    assert "Prácticas" in result


def test_strips_windows_1252_charset_meta():
    html = (
        '<html><head><meta charset="windows-1252"></head>'
        "<body><p>Gestión días Andrés</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "windows-1252" not in result.lower()
    assert "Gestión" in result
    assert "días" in result
    assert "Andrés" in result


def test_preserves_utf8_accented_characters_full_pipeline():
    html = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=us-ascii"></head>'
        "<body><p>Sí. Tenemos que tener firmado un convenio.</p>"
        "<p>Logroño (La Rioja)</p>"
        "<p>Teléfono: 941 299799</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "Sí" in result
    assert "Logroño" in result
    assert "Teléfono" in result
    # Ensure no mojibake sequences appear
    assert "Ã" not in result


def test_charset_normalisation_is_idempotent_on_correct_html():
    html = (
        '<html><head><meta charset="utf-8"></head>'
        "<body><p>Café résumé naïve</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "Café" in result
    assert "résumé" in result
    assert "naïve" in result
    assert "Ã" not in result


def test_charset_normalisation_skipped_without_head_tag():
    """Without a ``<head>``, no ``<meta charset>`` is injected — Python ``str``
    already arrives Unicode-decoded and prepending a ``<meta>`` before any
    DOCTYPE would push lxml into quirks mode.
    """
    html = '<p>Buenos días Andrés</p>'
    result = sanitize_email_html(html)
    assert "días" in result
    assert "Andrés" in result
    assert "Ã" not in result
    assert "<meta" not in result.lower()


# ---------------------------------------------------------------------------
# Geometry attribute restoration
# ---------------------------------------------------------------------------


def test_preserves_img_width_height_attributes():
    html = '<img src="https://example.com/logo.png" width="180" height="40">'
    result = sanitize_email_html(html)
    assert 'width="180"' in result
    assert 'height="40"' in result


def test_restores_width_from_inline_style_on_td():
    html = '<table><tr><td style="width: 500px">Text</td></tr></table>'
    result = sanitize_email_html(html)
    assert 'width="500"' in result


def test_restores_width_from_inline_style_on_table():
    html = '<table style="width: 600px"><tr><td>Cell</td></tr></table>'
    result = sanitize_email_html(html)
    assert 'width="600"' in result


def test_does_not_overwrite_existing_width_attribute():
    html = '<table width="400" style="width: 600px"><tr><td>Cell</td></tr></table>'
    result = sanitize_email_html(html)
    assert 'width="400"' in result


def test_signature_table_width_survives_full_pipeline():
    html = (
        "<html><head>"
        '<meta http-equiv="Content-Type" content="text/html; charset=us-ascii">'
        "<style>td.logo{width:200px} td.info{width:400px}</style>"
        "</head><body>"
        '<table width="600"><tr>'
        '<td class="logo"><img src="cid:logo" width="180" height="50"></td>'
        '<td class="info">Natalia Capilla San Juan</td>'
        "</tr></table></body></html>"
    )
    result = sanitize_email_html(html)
    assert "Natalia Capilla San Juan" in result
    # Table should keep its 600px width (either as attribute or via restoration)
    assert 'width="600"' in result
    # Image dimensions should survive
    assert 'width="180"' in result
    assert 'height="50"' in result
    # No mojibake
    assert "Ã" not in result


# ---------------------------------------------------------------------------
# Head-level tags leaking into body after flatten (Pencil.dev / Artlist /
# Eurofirms family of bugs). Everything under <head> must be removed so lxml's
# fragment parser does not reorganise the tree and drop body-level content.
# ---------------------------------------------------------------------------


def test_strips_title_tag_and_content_so_subject_does_not_leak():
    """Bug #36 (Pencil.dev): the <title> text was rendering as body content.

    Before Fix A, ``<title>Subject X</title>`` survived into the body-level
    fragment; bleach strips the tag but keeps the inner text, which the
    iframe then rendered as visible text.
    """
    html = (
        "<html><head><title>Pencil.dev: Introducing Code on Canvas</title>"
        '<meta charset="utf-8">'
        "</head><body><p>Real body content.</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "Real body content." in result
    assert "Introducing Code on Canvas" not in result


def test_strips_link_and_meta_tags_from_body_level():
    html = (
        "<html><head>"
        '<link rel="stylesheet" href="https://example.com/email.css">'
        '<meta name="viewport" content="width=device-width">'
        "</head><body><p>Visible</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "<link" not in result.lower()
    assert "stylesheet" not in result
    assert "viewport" not in result
    assert "Visible" in result


def test_strips_xml_island_leftover_from_mso_unwrap():
    """Some templates leak ``<xml>…</xml>`` at body level (Office island).

    Even after MSO unwrap/discard, a stray ``<xml>`` block can survive. If
    left in the fragment, lxml misreads it as generic markup and drops
    surrounding content.
    """
    html = (
        "<xml><o:OfficeDocumentSettings><o:AllowPNG/></o:OfficeDocumentSettings></xml>"
        "<p>Keep me.</p>"
    )
    result = sanitize_email_html(html)
    assert "Keep me." in result
    assert "OfficeDocumentSettings" not in result
    assert "<xml" not in result.lower()


# ---------------------------------------------------------------------------
# <body> background preservation (HubSpot Netcapital #8 and #9)
# ---------------------------------------------------------------------------


def test_preserves_body_background_color_style_in_wrapper():
    html = (
        '<html><body style="background-color:#eaeaff;margin:0">'
        "<p>content</p>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    # The background-color must survive inside a wrapping element so the
    # iframe's own white body does not win.
    assert "#eaeaff" in result
    assert "background-color" in result.lower()
    assert "content" in result


def test_preserves_body_bgcolor_attribute_in_wrapper():
    html = (
        '<html><body bgcolor="#fafafa">'
        "<p>content</p>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    # Legacy bgcolor must be promoted to inline background-color so that
    # the iframe's default white background doesn't cover it.
    assert "#fafafa" in result
    assert "background-color" in result.lower()
    assert "content" in result


def test_body_without_background_is_not_wrapped_unnecessarily():
    html = "<html><body><p>hi</p></body></html>"
    result = sanitize_email_html(html)
    # No synthetic wrapper div with a style attribute if there was nothing
    # to preserve.
    assert "<p>hi</p>" in result
    assert "background-color" not in result.lower()


# ---------------------------------------------------------------------------
# MSO / non-MSO coexistence (bug #20 Medusa, bug #73 Santander Open Academy)
# ---------------------------------------------------------------------------


def test_mso_hidden_and_non_mso_revealed_do_not_duplicate_content():
    """When the template ships both an Outlook-only block AND a non-Outlook
    revealed block with the same logical content, the output must not
    contain duplicates. The Outlook block is discarded (we're not Outlook).
    """
    html = (
        '<!--[if mso]>'
        '<table><tr><td><img src="https://cdn.example.com/hero.png" alt="HERO IMAGE"></td></tr></table>'
        '<![endif]-->'
        '<!--[if !mso]><!-- -->'
        '<table><tr><td><img src="https://cdn.example.com/hero.png" alt="HERO IMAGE"></td></tr></table>'
        '<!--<![endif]-->'
    )
    result = sanitize_email_html(html)
    assert result.count('alt="HERO IMAGE"') == 1


def test_mso_xml_island_discarded_keeps_following_content():
    html = (
        '<!--[if gte mso 9]><xml>'
        '<o:OfficeDocumentSettings><o:AllowPNG/></o:OfficeDocumentSettings>'
        '</xml><![endif]-->'
        '<p>Still here.</p>'
    )
    result = sanitize_email_html(html)
    assert "Still here." in result
    # The XML content inside the hidden conditional must be gone
    assert "OfficeDocumentSettings" not in result
    assert "AllowPNG" not in result


# ---------------------------------------------------------------------------
# Resilient <style> sanitisation (preheader bugs #5, #22, #51, #73, #84, #143, #315)
# ---------------------------------------------------------------------------


def test_preheader_display_none_rule_survives_sibling_broken_rule():
    """One bad CSS rule must not wipe the whole <style> block.

    Real templates frequently mix ``.preheader{display:none}`` (keeps the
    preview text hidden) with other rules that may fail to parse under
    cssutils' strict mode. Previously, a single parse failure wiped the
    whole block and the preheader leaked as visible text.
    """
    html = (
        "<html><head><style>"
        ".broken { width: calc(100% - ); }"
        ".preheader { display: none; max-height: 0; overflow: hidden; font-size: 0; }"
        "</style></head><body>"
        '<div class="preheader">PREHEADER TEXT</div>'
        "<p>Real body.</p>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    # premailer inlines .preheader {display:none} onto the div
    assert "display:none" in result.replace(" ", "").lower()
    assert "Real body." in result


# ---------------------------------------------------------------------------
# Full-body regression — MJML / tracking pixel patterns that previously lost
# the body entirely (Pencil.dev, Artlist, Eurofirms).
# ---------------------------------------------------------------------------


def test_mjml_pattern_with_doctype_title_meta_keeps_body_content():
    """Bug #36 Pencil.dev pattern: full DOCTYPE + head + MJML-style body.

    Input comes straight from the provider API with DOCTYPE, <html>,
    <head> (with <title>, <meta>, <style>) and <body> containing the
    actual newsletter tables. Before Fix A, lxml's fragment parser
    would reorganise the leftover head-level tags into a degenerate
    tree and drop large parts of the body.
    """
    html = (
        "<!DOCTYPE html>"
        '<html lang="en"><head>'
        "<title>Newsletter subject here</title>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        "<style>.wfix{margin:0 auto;max-width:600px}</style>"
        '</head><body style="background-color:#f0f0f0">'
        '<div class="wfix">'
        "<h1>NEWSLETTER HERO</h1>"
        "<p>Este es uno GRANDE. Introducción al código en el lienzo.</p>"
        '<table><tr><td><img src="https://cdn.example.com/hero.png" width="600"></td></tr></table>'
        "<p>Keep reading for more details…</p>"
        "</div>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    # The subject MUST NOT leak into the body
    assert "Newsletter subject here" not in result
    # The MJML body content MUST survive in full
    assert "NEWSLETTER HERO" in result
    assert "Este es uno GRANDE" in result
    assert "Keep reading" in result
    # The hero image survives — but as a signed proxy sentinel, not the raw
    # remote URL (the inbound sanitiser now rewrites remote images for privacy).
    assert f'src="{SENTINEL_PREFIX}' in result
    assert "cdn.example.com/hero.png" not in result


def test_tracking_pixel_does_not_replace_body():
    """Bug #56 Artlist pattern: body with visible content + 1×1 tracking pixel.

    The tracking pixel was the only surviving body element before the fix.
    """
    html = (
        "<!DOCTYPE html>"
        "<html><head><title>Marketing</title></head>"
        "<body>"
        '<div style="max-width:600px;margin:0 auto">'
        "<h1>The ultimate guide to Seedance 2.0</h1>"
        "<p>See what the new model can do in under 2 minutes.</p>"
        '<a href="https://artlist.io/read">Read more</a>'
        "</div>"
        '<img border="0" width="1" height="1" alt="" src="https://clicks.artlist.io/pixel.gif">'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert "The ultimate guide to Seedance 2.0" in result
    assert "See what the new model can do" in result
    assert "Read more" in result
    # Tracking pixel still present (as a proxy sentinel, not the raw URL) but is
    # NOT the only thing left.
    assert f'src="{SENTINEL_PREFIX}' in result
    assert "clicks.artlist.io" not in result


def test_head_with_font_description_does_not_leak_into_body():
    """Bug #473 Eurofirms pattern: email leaks Google Fonts README-like content.

    The input includes head-level elements that shouldn't render (the
    Quicksand description text was surviving, while the real body was
    dropped). After Fix A+D the real body content is visible.
    """
    html = (
        "<!DOCTYPE html>"
        "<html><head>"
        "<title>Mantén tu seguridad digital con Eurofirms</title>"
        '<meta charset="utf-8">'
        "</head><body>"
        "<h1>Mantén tu seguridad digital</h1>"
        "<p>Buenas prácticas de seguridad en Eurofirms.</p>"
        "<p>Activa la autenticación en dos pasos y cambia tus contraseñas regularmente.</p>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert "Mantén tu seguridad digital" in result
    assert "Buenas prácticas" in result
    assert "autenticación en dos pasos" in result
    # Title text doesn't leak (already in body as <h1>, but there's only 1 occurrence)
    # No mojibake
    assert "Ã" not in result


# ---------------------------------------------------------------------------
# Robustness regressions for the sixth-round pipeline hardening (this PR)
# ---------------------------------------------------------------------------


def test_body_style_with_double_quotes_inside_single_quoted_attr():
    """A single-quoted ``<body style='…"…"…'>`` triggered the bug: the old
    ``replace('"', "'")`` rewrote the inner double quotes into single ones,
    producing a wrapper div with mismatched quoting that bleach/lxml could
    misparse. After Fix 2 the attribute is HTML-escaped (``&quot;``), so the
    wrapper ``<div>`` is always well-formed and the body survives intact.
    """
    html = (
        "<html><body style='font-family:\"Helvetica Neue\",Arial,"
        "sans-serif;background-color:#eaeaff'>"
        "<p>contenido visible</p>"
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert "contenido visible" in result
    # Background must still be promoted to the wrapper div.
    assert "#eaeaff" in result
    # The original <body> tag is gone (flattened into a wrapper div).
    assert "<body" not in result.lower()
    # No half-escaped attribute leftover that would render as visible text.
    assert "font-family" in result.lower()


def test_geometry_mirror_preserves_count_when_lxml_normalises_entities():
    """lxml normalises ``&`` → ``&amp;`` and re-quotes attributes, so the
    serialised output is often *longer* than the input. Before Fix 1 the
    50%-byte guard could (in adversarial cases) trigger on innocent input.
    The new guard counts critical layout elements (img/td/th/table) before
    and after, so cosmetic byte deltas no longer matter.
    """
    html = (
        '<table width="600"><tr>'
        '<td style="width:300px"><a href="https://example.com/?a=1&amp;b=2&amp;c=3'
        '&amp;d=4&amp;e=5&amp;f=6&amp;g=7">link</a></td>'
        '<td style="height:120px"><img src="https://x.test/i.png" '
        'style="width:200px;height:80px" alt="x"></td>'
        "</tr></table>"
    )
    result = sanitize_email_html(html)
    # Geometry mirroring must apply: width/height end up as attributes.
    assert 'width="300"' in result
    assert 'height="120"' in result
    assert 'width="200"' in result
    assert 'height="80"' in result
    # Original table width must survive as well.
    assert 'width="600"' in result


# ---------------------------------------------------------------------------
# Legacy ``background`` attribute — table/cell background-image carrier
# (AliExpress EDM product grids and similar templates render the thumbnail
# through ``<td background="https://…">`` instead of an ``<img>``).
# ---------------------------------------------------------------------------


def test_preserves_background_attribute_on_td():
    """The thumbnail URL lives in the ``background`` attribute on the cell.

    Before the attribute was allow-listed, bleach dropped it and only the
    placeholder ``background-color`` remained, so each product cell rendered as
    a grey box (the AliExpress "Ofertas imprescindibles" grid bug).
    """
    html = (
        '<table><tr><td '
        'background="https://ae01.alicdn.com/kf/thumb.png" '
        'style="background-size:cover;background-color:rgba(0,0,0,0.2)">.</td></tr></table>'
    )
    result = sanitize_email_html(html)
    # The ``background`` attribute is still allow-listed (not dropped to a grey
    # box), but the remote URL is rewritten to a signed proxy sentinel.
    assert f'background="{SENTINEL_PREFIX}' in result
    assert "ae01.alicdn.com" not in result


def test_preserves_background_attribute_on_table():
    html = '<table background="https://cdn.example.com/bg.png"><tr><td>x</td></tr></table>'
    result = sanitize_email_html(html)
    # Allow-listed attribute survives; the remote URL becomes a proxy sentinel.
    assert f'background="{SENTINEL_PREFIX}' in result
    assert "cdn.example.com/bg.png" not in result


def test_preserves_data_url_background_attribute_from_resolved_cid():
    """``inline_cid_images`` rewrites ``background="cid:…"`` into a ``data:`` URL
    on the same attribute, so the resolved value must survive bleach too.
    """
    html = (
        '<table><tr><td background="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==">'
        '.</td></tr></table>'
    )
    result = sanitize_email_html(html)
    assert 'background="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="' in result


def test_strips_javascript_background_attribute():
    """``background`` is a URI attribute, so the protocol allowlist must be
    enforced on it exactly like on ``src``/``href``.
    """
    html = '<table><tr><td background="javascript:alert(1)">.</td></tr></table>'
    result = sanitize_email_html(html)
    assert "javascript:" not in result


# ---------------------------------------------------------------------------
# Widened allowlist — legacy table/geometry attributes, semantic HTML5 tags,
# column machinery, tel: links and modern CSS. With ``strip=True`` a missing
# tag keeps the text but silently loses the styling it carried, so these pin
# that the styling survives.
# ---------------------------------------------------------------------------


def test_preserves_tr_bgcolor_and_height():
    html = '<table><tr bgcolor="#f4f4f4" height="40"><td>fila</td></tr></table>'
    result = sanitize_email_html(html)
    assert 'bgcolor="#f4f4f4"' in result
    assert 'height="40"' in result


def test_preserves_img_hspace_vspace():
    html = '<img src="https://x.test/logo.png" hspace="10" vspace="5" alt="logo">'
    result = sanitize_email_html(html)
    assert 'hspace="10"' in result
    assert 'vspace="5"' in result


def test_preserves_table_height_attribute():
    html = '<table height="300"><tr><td>x</td></tr></table>'
    result = sanitize_email_html(html)
    assert 'height="300"' in result


def test_preserves_nowrap_on_td():
    html = '<table><tr><td nowrap>sin saltos</td></tr></table>'
    result = sanitize_email_html(html)
    assert "nowrap" in result
    assert "sin saltos" in result


def test_preserves_semantic_wrapper_with_its_style():
    """HTML5 semantic wrappers keep their tag and inline style instead of
    being stripped to bare text (a ``<section style="background:…">`` used
    to lose its background even though the text survived).
    """
    html = '<section style="background-color:#101820"><p>contenido</p></section>'
    result = sanitize_email_html(html)
    assert "<section" in result
    assert "#101820" in result
    assert "contenido" in result


def test_preserves_colgroup_and_col_geometry():
    html = (
        '<table><colgroup><col span="2" width="120"></colgroup>'
        "<tr><td>a</td><td>b</td></tr></table>"
    )
    result = sanitize_email_html(html)
    assert "<col" in result
    assert 'span="2"' in result
    assert 'width="120"' in result


def test_preserves_flexbox_inline_css():
    html = '<div style="display:flex;flex-direction:column;gap:8px">x</div>'
    result = sanitize_email_html(html)
    normalized = result.replace(" ", "").lower()
    assert "display:flex" in normalized
    assert "flex-direction:column" in normalized
    assert "gap:8px" in normalized


def test_allows_tel_href():
    html = '<a href="tel:+34941299799">Llámanos</a>'
    result = sanitize_email_html(html)
    assert 'href="tel:+34941299799"' in result


# ---------------------------------------------------------------------------
# Inbound link hardening — the viewer iframe is sandboxed WITH popups
# (``allow-popups allow-popups-to-escape-sandbox``), so a clicked link opens
# a real tab. Every <a> must sever window.opener (reverse tabnabbing) and
# the image-only protocols (cid:/data:) must not ride on href.
# ---------------------------------------------------------------------------


def test_forces_target_blank_and_noopener_on_links():
    result = sanitize_email_html('<a href="https://example.com">link</a>')
    assert 'target="_blank"' in result
    assert "noopener" in result
    assert "noreferrer" in result


def test_link_hardening_overrides_sender_target_and_rel():
    result = sanitize_email_html(
        '<a href="https://example.com" target="_self" rel="opener">x</a>'
    )
    assert 'target="_blank"' in result
    assert 'target="_self"' not in result
    assert 'rel="opener"' not in result


def test_drops_data_href_on_anchor_but_keeps_data_img_src():
    html = (
        '<a href="data:text/html,pwned">enlace</a>'
        '<img src="data:image/png;base64,AAA" alt="ok">'
    )
    result = sanitize_email_html(html)
    assert 'href="data:' not in result
    assert "enlace" in result  # the link text survives, only the href drops
    assert 'src="data:image/png;base64,AAA"' in result


def test_drops_cid_href_on_anchor():
    result = sanitize_email_html('<a href="cid:parte-interna">ver</a>')
    assert 'href="cid:' not in result
    assert "ver" in result


def test_keeps_fragment_href_on_anchor():
    # Scheme-less hrefs (fragments / relative) carry no scheme to filter on
    # and stay untouched.
    result = sanitize_email_html('<a href="#seccion">ir</a>')
    assert 'href="#seccion"' in result


# ---------------------------------------------------------------------------
# Remote-image proxy rewrite — ``sanitize_email_html`` is no longer a pure alias
# of ``prepare_email_html``: it composes the pipeline with the remote-image
# rewrite, so every cached body carries signed proxy sentinels instead of raw
# remote image URLs (privacy). These pin the end-to-end round trip through the
# real signer, across all four rewrite surfaces.
# ---------------------------------------------------------------------------


# Matches the sentinel in either an attribute (``&amp;`` between params) or a
# ``<style>`` rawtext ``url(...)`` (raw ``&``), capturing the ``u`` / ``s`` pair.
_SENTINEL_US_RE = re.compile(
    re.escape(SENTINEL_PREFIX) + r"\?u=([^&\"'<>)\s]+)(?:&amp;|&)s=([^&\"'<>)\s]+)"
)


def _extract_original_url(result: str) -> str:
    """Pull the sentinel out of a sanitised body and verify+decode it back."""
    match = _SENTINEL_US_RE.search(result)
    assert match, f"no sentinel found in: {result}"
    original = verify_and_extract(match.group(1), match.group(2))
    assert original is not None, "sentinel signature failed to verify"
    return original


def test_remote_img_src_rewritten_to_verifiable_sentinel():
    result = sanitize_email_html('<img src="https://cdn.example.com/logo.png">')
    assert SENTINEL_PREFIX in result
    assert _extract_original_url(result) == "https://cdn.example.com/logo.png"


def test_remote_td_background_rewritten_to_verifiable_sentinel():
    result = sanitize_email_html(
        '<table><tr><td background="https://cdn.example.com/bg.png">.</td></tr></table>'
    )
    assert _extract_original_url(result) == "https://cdn.example.com/bg.png"


def test_inline_style_background_url_rewritten_to_verifiable_sentinel():
    result = sanitize_email_html(
        '<div style="background-image:url(https://cdn.example.com/hero.png)">x</div>'
    )
    assert _extract_original_url(result) == "https://cdn.example.com/hero.png"


def test_style_block_url_query_ampersand_round_trips_unescaped():
    """The load-bearing ``<style>`` trap: bleach HTML-escapes ``<style>`` content
    (``&`` → ``&amp;``) and lxml hands it back verbatim, so the rewrite must
    UNESCAPE the URL before signing. A background URL carrying ``?a=1&b=2`` must
    verify back to exactly ``?a=1&b=2`` (raw ``&``), never ``?a=1&amp;b=2``.
    The URL lives inside ``@media`` so premailer does not inline it away.
    """
    html = (
        "<html><head><style>"
        "@media screen{.hero{background:url(https://cdn.example.com/bg.png?a=1&b=2)}}"
        "</style></head><body><div class=\"hero\">x</div></body></html>"
    )
    result = sanitize_email_html(html)
    assert _extract_original_url(result) == "https://cdn.example.com/bg.png?a=1&b=2"


def test_cid_and_data_images_are_not_proxied():
    """Only remote ``http(s)`` images are rewritten — ``cid:`` / ``data:`` stay."""
    html = (
        '<img src="cid:logo@x">'
        '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==">'
    )
    result = sanitize_email_html(html)
    assert SENTINEL_PREFIX not in result
    assert 'src="cid:logo@x"' in result
    assert 'src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="' in result


def test_remote_anchor_href_is_not_proxied():
    """Only images are rewritten — a remote ``<a href>`` link is left intact."""
    result = sanitize_email_html('<a href="https://example.com/page">link</a>')
    assert SENTINEL_PREFIX not in result
    assert 'href="https://example.com/page"' in result


# ---------------------------------------------------------------------------
# Premailer network isolation — ``allow_network=False`` is load-bearing: the
# default downloads every ``<link rel="stylesheet">`` the sender planted
# (server-side, unguarded — SSRF + read tracking) and, with
# ``keep_style_tags=True``, injects the downloaded body VERBATIM as ``<style>``
# text. A downloaded HTML page then escapes the block on the next reparse and
# leaks as visible markup (the real-world Eurofirms/Google-Fonts "texto
# extraño" bug).
# ---------------------------------------------------------------------------


def test_pipeline_never_fetches_link_stylesheets(monkeypatch):
    """The pipeline must not perform ANY network fetch for sender stylesheets.

    The fake records every ``get`` and returns valid CSS (never raises), so a
    regression to ``allow_network=True`` cannot hide behind the pipeline's
    fail-soft premailer wrapper — the recorded call itself is the failure.
    """
    import premailer.premailer as premailer_module

    calls: list[str] = []

    class _FakeResponse:
        text = "p{color:red}"

        def raise_for_status(self):
            return None

    class _RecordingRequests:
        def get(self, url, **kwargs):
            calls.append(url)
            return _FakeResponse()

    monkeypatch.setattr(premailer_module, "requests", _RecordingRequests())
    html = (
        "<html><head>"
        '<link rel="stylesheet" href="https://sender-tracker.example/style.css">'
        "</head><body><p>Cuerpo real del correo</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert calls == [], f"el saneado descargó URLs del remitente: {calls}"
    assert "Cuerpo real del correo" in result
    assert "sender-tracker.example" not in result


def test_link_stylesheet_content_is_never_injected(monkeypatch):
    """Even if a fetch were attempted, no downloaded markup may reach the body.

    Simulates the old failure end-to-end: a ``<link>`` whose target returns an
    HTML page containing ``</style>`` (the escape vector). With
    ``allow_network=False`` premailer never consumes it, so none of the fetched
    content — visible text nor markup — can appear in the sanitised body.
    """
    import premailer.premailer as premailer_module

    fetched = (
        "<html><body><style>.x{color:red}</style>"
        "<h1>Quicksand</h1><p>Quicksand is a display sans serif.</p></body></html>"
    )

    class _FakeResponse:
        text = fetched

        def raise_for_status(self):
            return None

    class _FakeRequests:
        def get(self, url, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(premailer_module, "requests", _FakeRequests())
    html = (
        "<html><head>"
        '<link rel="stylesheet" href="https://fonts.example/specimen.css">'
        "</head><body><p>Oferta de empleo</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "Oferta de empleo" in result
    assert "Quicksand" not in result
    assert "sans serif" not in result


# ---------------------------------------------------------------------------
# <style> rawtext unescape (step 8) — combinators must reach the CSS parser raw
# ---------------------------------------------------------------------------


def test_style_block_child_combinator_survives_unescaped():
    """``>`` combinators in <style> must stay raw characters, never ``&gt;``.

    ``<style>`` is rawtext: the browser hands its content to the CSS parser
    without entity-decoding, so an ``&gt;`` reaches it verbatim and the
    entity's ``;`` SPLITS the selector (everything before it is discarded as
    an invalid declaration; the remainder parses as a fresh rule).
    """
    html = (
        "<html><head><style>"
        ".wrapper > table { border-collapse: collapse }"
        "</style></head><body><table><tr><td>x</td></tr></table></body></html>"
    )
    result = sanitize_email_html(html)
    assert "&gt;" not in result
    assert re.search(r"\.wrapper\s*>\s*table", result)


def test_scoped_dark_rule_does_not_degenerate_into_global_rule():
    """The Amazon.es regression: an Outlook dark-hack rule with a child
    combinator must keep its ``[data-ogsc]`` scope intact.

    Before the fix the escaped ``&gt;`` split the selector at CSS-parse time
    and the remainder became a global ``table { background:#181a1a }``,
    painting every table dark in the light viewer.
    """
    html = (
        "<html><head><style>"
        "[data-ogsc] .rio-card > table { background-color: #181a1a }"
        "</style></head><body>"
        '<table bgcolor="white"><tr><td>contenido</td></tr></table>'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    match = re.search(r"<style[^>]*>(.*?)</style>", result, re.S)
    assert match is not None
    css = match.group(1)
    # The scope prefix and the combinator survive together on one selector —
    # no entity, no split.
    assert "&gt;" not in css
    assert re.search(r"\[data-ogsc\]\s*\.rio-card\s*>\s*table", css)


def test_style_unescape_leaves_lt_escaped():
    """``&lt;`` must NEVER be unescaped inside <style> (it could materialise a
    premature ``</style>`` and break out of the block). ``&gt;``/``&amp;`` are
    restored; ``&lt;`` stays an entity (a broken CSS token, harmless)."""
    from api.services.email_html_pipeline import _unescape_style_blocks

    html = "<style>a &lt;/style&gt; b { color: red }</style>"
    result = _unescape_style_blocks(html)
    assert "&lt;" in result
    # The closing sequence never materialises early: exactly one real
    # ``</style>`` (the block's own closer) exists in the output.
    assert result.count("</style>") == 1
    assert "&gt;" not in result


def test_style_unescape_double_escaped_gt_stays_entity():
    """A literal ``&gt;`` in the ORIGINAL CSS arrives double-escaped
    (``&amp;gt;``) and must collapse to the entity ``&gt;`` — a broken CSS
    token — not to a live ``>`` (that would change the author's data)."""
    from api.services.email_html_pipeline import _unescape_style_blocks

    html = "<style>i &amp;gt; j { color: red }</style>"
    result = _unescape_style_blocks(html)
    assert "&gt;" in result
    assert "&amp;gt;" not in result


# ---------------------------------------------------------------------------
# prefers-color-scheme media queries — dropped whole (light-only viewer)
# ---------------------------------------------------------------------------


def test_drops_dark_scheme_media_query_keeps_responsive_ones():
    """``@media (prefers-color-scheme: dark)`` blocks are dropped whole (the
    viewer is light-only and Gmail strips them too); responsive ``@media``
    blocks survive untouched."""
    html = (
        "<html><head><style>"
        ".card { background: #fff }"
        "@media (prefers-color-scheme: dark) { .card { background: #181a1a } }"
        "@media only screen and (max-width: 480px) { .card { width: 100% } }"
        "</style></head><body><div class=\"card\">x</div></body></html>"
    )
    result = sanitize_email_html(html)
    assert "prefers-color-scheme" not in result
    assert "#181a1a" not in result
    assert "max-width" in result


def test_drops_light_scheme_media_query_too():
    """``prefers-color-scheme: light`` blocks are redundant with the base
    design once the viewer is pinned to light — dropped for symmetry (matches
    Gmail, which strips every prefers-color-scheme query)."""
    html = (
        "<html><head><style>"
        "@media (prefers-color-scheme: light) { .card { background: #fafafa } }"
        "</style></head><body><div class=\"card\">x</div></body></html>"
    )
    result = sanitize_email_html(html)
    assert "prefers-color-scheme" not in result


def test_drops_combined_condition_dark_media_query():
    """A media query mixing screen/width conditions WITH prefers-color-scheme
    is still dropped — the dark condition must never fire, and Gmail drops the
    whole query as well."""
    html = (
        "<html><head><style>"
        "@media screen and (max-width: 600px) and (prefers-color-scheme: dark)"
        " { .m { background: #000 } }"
        "</style></head><body><div class=\"m\">x</div></body></html>"
    )
    result = sanitize_email_html(html)
    assert "prefers-color-scheme" not in result
    assert "background: #000" not in result


# ---------------------------------------------------------------------------
# var() custom-property uses — dropped so legacy fallbacks wake up
# ---------------------------------------------------------------------------


def test_var_use_dropped_from_style_block():
    """Declarations using ``var(…)`` are dropped from <style> rules: their
    ``--x`` definitions never survive sanitisation, so the use would compute
    to *invalid at computed-value time* and wipe the element's background."""
    html = (
        "<html><head><style>"
        "body { background-color: var(--body-bg); color: #111 }"
        "</style></head><body><p>x</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "var(" not in result
    assert "color: #111" in result or "color:#111" in result.replace(" ", "")


def test_var_use_dropped_from_inline_style_wakes_bgcolor_fallback():
    """The Amazon.es body pattern: ``style="background-color:var(--body-bg)"``
    plus a legacy ``bgcolor="#FFFFFF"`` fallback. The broken var() use must be
    dropped so the promoted body wrapper carries the bgcolor fallback."""
    html = (
        "<html><head></head>"
        '<body style="background-color:var(--body-bg); color:var(--body-color)"'
        ' bgcolor="#FFFFFF"><p>hola</p></body></html>'
    )
    result = sanitize_email_html(html)
    assert "var(" not in result
    normalized = result.replace(" ", "").lower()
    assert "background-color:#ffffff" in normalized


def test_var_use_dropped_from_element_inline_style():
    """var() uses on regular elements (not just the body) are dropped by the
    bleach CSSSanitizer subclass while sibling declarations survive."""
    html = (
        '<div style="background-color: var(--x); padding: 4px">x</div>'
    )
    result = sanitize_email_html(html)
    assert "var(" not in result
    assert "padding: 4px" in result or "padding:4px" in result.replace(" ", "")


# ---------------------------------------------------------------------------
# Attribute entity double-escaping (bleach single-quoted serialisation)
# ---------------------------------------------------------------------------


def test_inline_style_with_double_quote_signs_the_decoded_image_url():
    """The regression that broke images at random.

    ``CSSSanitizer`` normalises ``font-family:'X Y'`` to double quotes, which
    makes bleach serialise the whole ``style`` attribute with SINGLE quotes —
    and on that branch bleach skipped its own ``escape_base_amp``, so every
    ``&`` already sitting there as ``&amp;`` came out as ``&amp;amp;``. The
    image rewrite then signed a URL carrying a literal ``&amp;``, and the
    proxy asked the CDN for an ``amp;h`` parameter: a permanently broken
    image, baked into the immutable cached body together with its signature.
    """
    url = "https://cdn.example.com/hero.png?w=600&h=400"
    html = (
        "<div style=\"font-family:'Helvetica Neue',Arial;"
        f"background-image:url('{url}')\">x</div>"
    )
    result = sanitize_email_html(html)
    assert "&amp;amp;" not in result
    match = re.search(
        re.escape(SENTINEL_PREFIX) + r"\?u=(?P<u>[^&\"'\s)]+)&(?:amp;)?s=(?P<s>[0-9a-f]+)",
        result,
    )
    assert match is not None
    assert verify_and_extract(match.group("u"), match.group("s")) == url


def test_attribute_entities_are_not_double_escaped_when_value_holds_a_quote():
    """Same serialiser bug seen on plain text attributes: an ``alt`` carrying
    both a double quote and an ``&amp;`` rendered as a literal ``&amp;``."""
    html = '<img src="https://cdn.example.com/a.png" alt=\'Tom &amp; Jerry "best"\'>'
    result = sanitize_email_html(html)
    assert "&amp;amp;" not in result


def test_bare_ampersand_in_quoted_attribute_is_still_escaped():
    """The fix must not relax escaping: a bare ``&`` still becomes ``&amp;``
    (it is the same ``escape_base_amp`` bleach applies to double-quoted
    values), and an ambiguous entity stays neutralised."""
    result = sanitize_email_html('<div title=\'say "hi" & bye &notanentity; end\'>x</div>')
    assert "&amp; bye" in result
    assert "&amp;notanentity;" in result


# ---------------------------------------------------------------------------
# !important preservation (responsive @media rules must beat inlined styles)
# ---------------------------------------------------------------------------


def test_important_survives_in_the_preserved_style_block():
    """Premailer's default strips ``!important`` from every declaration it
    touches, including the ``<style>`` block it preserves. Email templates use
    it inside ``@media`` precisely to beat the inline styles premailer just
    injected, so stripping it made the mobile rules lose the cascade and the
    email rendered with its desktop layout inside the narrow viewer."""
    html = (
        "<html><head><style>"
        ".wrapper{width:600px}"
        "@media only screen and (max-width:600px){"
        ".wrapper{width:100% !important}.col{display:block !important}}"
        "</style></head><body>"
        '<table class="wrapper" width="600"><tr><td class="col">x</td></tr></table>'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert "width: 100% !important" in result
    assert "display: block !important" in result


# ---------------------------------------------------------------------------
# <body background="..."> — legacy full-page background image
# ---------------------------------------------------------------------------


def test_body_background_attribute_is_promoted_to_the_wrapper_div():
    """``<body>`` does not survive flattening, so its legacy ``background``
    image attribute has to be promoted onto the wrapper div exactly like
    ``bgcolor`` — otherwise the whole-email background silently disappears.
    It is a remote image, so it must come back proxied, never raw."""
    html = (
        '<html><body background="https://cdn.example.com/paper.png" '
        'bgcolor="#eeeeee"><p>hola</p></body></html>'
    )
    result = sanitize_email_html(html)
    assert "background-image" in result
    assert "cdn.example.com" not in result
    assert SENTINEL_PREFIX in result


# ---------------------------------------------------------------------------
# Protocol-relative image URLs (//host/path) must be proxied, never leaked
# ---------------------------------------------------------------------------


def test_protocol_relative_image_url_is_proxied():
    """Inside the viewer's ``srcdoc`` iframe a ``//host/path`` reference
    resolves against the app's own base URL, so the browser would fetch it
    straight from the sender's CDN — the IP leak the proxy exists to prevent."""
    result = sanitize_email_html('<img src="//cdn.example.com/logo.png" width="20">')
    assert "cdn.example.com" not in result
    match = re.search(
        re.escape(SENTINEL_PREFIX) + r"\?u=(?P<u>[^&\"'\s)]+)&(?:amp;)?s=(?P<s>[0-9a-f]+)",
        result,
    )
    assert match is not None
    assert verify_and_extract(match.group("u"), match.group("s")) == (
        "https://cdn.example.com/logo.png"
    )


# ---------------------------------------------------------------------------
# @font-face — dropped so no remote reference escapes the image proxy
# ---------------------------------------------------------------------------


def test_font_face_at_rule_is_dropped_entirely():
    """A web font's ``src: url(…)`` is the one remote reference the image
    rewrite never proxies (the proxy serves ``image/*`` only), so a surviving
    ``@font-face`` had the browser fetch it STRAIGHT from the sender's host —
    a read-tracking channel that bypasses the proxy. Measured at 108
    references across 31% of a real corpus before removal. Gmail strips the
    at-rule too, so this is rendering parity."""
    html = (
        "<html><head><style>"
        "@font-face{font-family:Fancy;src:url(https://fonts.example.com/f.woff2)}"
        ".t{color:#111}"
        "</style></head><body><p class=\"t\">x</p></body></html>"
    )
    result = sanitize_email_html(html)
    assert "@font-face" not in result
    assert "fonts.example.com" not in result
    # Sibling rules in the same block are untouched.
    assert "color: #111" in result or "color:#111" in result.replace(" ", "")


def test_font_face_removal_leaves_no_remote_url_behind():
    """The whole point of the removal: after sanitisation the body must carry
    no remote reference outside a signed proxy sentinel."""
    html = (
        "<html><head><style>"
        "@font-face{font-family:A;src:url('https://fonts.gstatic.com/s/a.woff2') format('woff2')}"
        "@media (max-width:600px){.c{width:100%!important}}"
        "</style></head><body>"
        '<img src="https://cdn.example.com/x.png"><div class="c">y</div>'
        "</body></html>"
    )
    result = sanitize_email_html(html)
    assert "gstatic.com" not in result
    assert "cdn.example.com" not in result
    assert SENTINEL_PREFIX in result
    # The responsive rule that shares the block still survives.
    assert "max-width" in result and "!important" in result


def test_src_is_not_an_allowed_css_property_any_more():
    """``src`` was allowlisted solely for ``@font-face``; with the at-rule gone
    it is dead surface, and keeping it would let a ``url()`` slip past the
    per-property filter of the ``<style>`` rewrite."""
    from api.services.email_html_pipeline import (
        _ALLOWED_CSS_AT_RULES,
        _ALLOWED_CSS_PROPERTIES,
    )

    assert "font-face" not in _ALLOWED_CSS_AT_RULES
    assert "src" not in _ALLOWED_CSS_PROPERTIES
