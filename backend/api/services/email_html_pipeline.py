"""HTML rendering pipeline for email content.

Transforms provider-supplied HTML (Gmail MIME parts, Outlook Graph body) into
browser-safe HTML for the sandboxed iframe viewer. All functions in this module
are pure — no DB, no auth, no network. ``prepare_email_html`` is the single
entry point and composes the individual steps in the correct order.

Pipeline
--------
1. ``_normalize_charset_meta`` — strip bogus ``<meta charset>`` / ``<meta
   http-equiv>`` declarations (Outlook injects ``charset=us-ascii``) and inject
   a canonical UTF-8 meta so lxml/html5lib treat the input as UTF-8.
2. ``_unwrap_mso_conditionals`` — unwrap ``<!--[if mso | IE]>…<![endif]-->``
   and ``<!--[if !mso]>…<![endif]-->`` so the desktop layout used by Outlook
   templates survives the later comment-stripping pass.
3. ``_sanitize_style_blocks`` — parse each ``<style>`` block with cssutils,
   filter properties against the allowlist, drop unsafe at-rules (``@import``,
   ``@namespace``, ``@charset``) and keep ``@media`` / ``@supports`` /
   ``@font-face`` / style rules. Also drops property values containing
   ``expression(…)`` or ``javascript:`` schemes.
4. ``_inline_css_via_premailer`` — inline style rules into ``style=""``
   attributes for parity with mail clients that strip ``<style>`` blocks. The
   sanitized ``<style>`` block is kept (``keep_style_tags=True``) so ``@media``
   queries and pseudo-classes survive and render correctly in the iframe.
5. ``_mirror_geometry_to_attributes`` — mirror ``width``/``height`` values
   from inline styles back onto HTML attributes on ``<img>``/``<td>``/``<th>``/
   ``<table>``. Gives signature tables and inline logos a second fallback if
   the style is ever lost downstream.
6. ``_strip_script_blocks`` — remove ``<script>`` blocks entirely (content
   included). ``<style>`` is intentionally left alone now; its content was
   sanitized in step 3 and is preserved through bleach.
7. ``_clean_with_bleach`` — final tag/attribute/protocol allowlist. Inline
   ``style=""`` attributes are filtered through ``CSSSanitizer``. The same
   pass hardens every surviving ``<a>`` (forced ``target="_blank"`` +
   ``rel="noopener noreferrer"``, and ``href`` dropped when its scheme is
   ``cid:``/``data:`` — those are image-only protocols) via the shared
   :mod:`api.services.html_link_hardening` filter.
"""

from __future__ import annotations

import logging
import re
from html import escape as html_escape
from html import unescape as html_unescape
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Includes the HTML5 semantic/structural wrappers (``section``, ``article``,
# ``header``, ``footer``, ``figure``, …) and the table column machinery
# (``caption``/``col``/``colgroup``): with ``strip=True`` bleach removes a
# disallowed tag but keeps its children, so leaving these out silently
# discards any ``style=""``/geometry they carry (a ``<section
# style="background:…">`` loses its background) even though the text
# survives. All of them are inert containers — no scripting, no navigation.
_ALLOWED_TAGS: list[str] = [
    "a", "abbr", "address", "article", "aside", "b", "big", "blockquote",
    "br", "caption", "center", "cite", "code", "col", "colgroup", "dd",
    "del", "dfn", "div", "dl", "dt", "em", "figcaption", "figure", "font",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i",
    "img", "ins", "kbd", "li", "main", "mark", "nav", "ol", "p", "pre",
    "q", "s", "samp", "section", "small", "span", "strike", "strong",
    "style", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "time", "tr", "tt", "u", "ul", "var", "wbr",
]

# ``background`` is the legacy HTML attribute that points a table/cell at a
# background image (``<td background="https://…">``). Email templates lean on
# it heavily as an alternative to ``<img>`` for product thumbnails and hero
# tiles (e.g. AliExpress EDM grids). It MUST stay in the allowlist or bleach
# drops it, leaving only the placeholder ``background-color`` and rendering the
# cell as a grey box. bleach treats ``background`` as a URI attribute, so the
# protocol allowlist below is still enforced on it (``javascript:`` is stripped
# exactly like on ``src``/``href``).
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "*": ["class", "id", "style", "dir", "lang", "title", "align", "valign"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height", "border", "hspace", "vspace"],
    "td": ["colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "background", "nowrap"],
    "th": ["colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "background", "nowrap"],
    "tr": ["bgcolor", "height"],
    "table": ["border", "cellpadding", "cellspacing", "width", "height", "align", "bgcolor", "background"],
    "col": ["span", "width", "bgcolor"],
    "colgroup": ["span", "width", "bgcolor"],
    "font": ["color", "size", "face"],
    "ol": ["start", "type"],
}

# ``tel`` covers the "call us" footer links real emails carry; it navigates
# to the OS dialer, never executes content. ``cid``/``data`` are needed for
# inline images (``img src`` / ``td background``) — on ``<a href>`` they are
# stripped again by the link-hardening filter in ``_clean_with_bleach``.
_ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto", "tel", "cid", "data"]

# CSS properties safe to keep inside ``style=""`` attributes and inside
# ``<style>`` rules. Covers the vocabulary real email templates use (layout,
# colors, typography, spacing, borders) without opening the door to properties
# that pull in remote resources or execute logic.
_ALLOWED_CSS_PROPERTIES: frozenset[str] = frozenset({
    "align-content", "align-items", "align-self", "background",
    "background-attachment", "background-clip", "background-color",
    "background-image", "background-origin",
    "background-position", "background-repeat", "background-size", "border",
    "border-bottom", "border-bottom-color", "border-bottom-left-radius",
    "border-bottom-right-radius", "border-bottom-style", "border-bottom-width",
    "border-collapse", "border-color", "border-left", "border-left-color",
    "border-left-style", "border-left-width", "border-radius", "border-right",
    "border-right-color", "border-right-style", "border-right-width",
    "border-spacing", "border-style", "border-top", "border-top-color",
    "border-top-left-radius", "border-top-right-radius", "border-top-style",
    "border-top-width", "border-width", "bottom", "box-shadow", "box-sizing",
    "caption-side", "clear", "color", "column-gap", "direction", "display",
    "empty-cells", "flex", "flex-basis", "flex-direction", "flex-flow",
    "flex-grow", "flex-shrink", "flex-wrap", "float",
    "font", "font-family", "font-size", "font-stretch", "font-style",
    "font-variant", "font-weight", "gap", "height", "inset",
    "justify-content", "justify-items", "justify-self", "left",
    "letter-spacing", "line-height", "list-style", "list-style-image",
    "list-style-position",
    "list-style-type", "margin", "margin-block", "margin-block-end",
    "margin-block-start", "margin-bottom", "margin-inline",
    "margin-inline-end", "margin-inline-start", "margin-left", "margin-right",
    "margin-top", "max-height", "max-width", "min-height", "min-width",
    "mso-line-height-rule", "mso-table-lspace", "mso-table-rspace",
    "object-fit", "object-position", "opacity", "order",
    "outline", "overflow", "overflow-wrap", "overflow-x", "overflow-y",
    "padding", "padding-block", "padding-block-end", "padding-block-start",
    "padding-bottom", "padding-inline", "padding-inline-end",
    "padding-inline-start", "padding-left", "padding-right", "padding-top",
    "page-break-after", "page-break-before", "position", "right", "row-gap",
    "src", "table-layout", "text-align", "text-decoration",
    "text-decoration-color", "text-decoration-line", "text-decoration-style",
    "text-decoration-thickness", "text-indent",
    "text-overflow", "text-shadow", "text-transform", "top", "unicode-bidi",
    "vertical-align",
    "visibility", "white-space", "width", "word-break", "word-spacing",
    "word-wrap", "z-index",
})

# At-rules kept inside ``<style>`` blocks. ``@import`` / ``@namespace`` /
# ``@charset`` are dropped (can fetch remote resources or change parsing) —
# ``@media`` and ``@supports`` carry responsive layouts and must survive.
# ``@font-face`` is kept so corporate signatures with web fonts still render.
_ALLOWED_CSS_AT_RULES: frozenset[str] = frozenset({"media", "supports", "font-face"})


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Outlook-generated HTML routinely declares a bogus charset in the <head>
# (``<meta http-equiv="Content-Type" content="…; charset=us-ascii">`` or
# ``<meta charset="windows-1252">``) that does not match the actual UTF-8
# bytes. Handing that to lxml (premailer) or html5lib (bleach) makes both
# parsers honour the wrong charset and silently re-interpret UTF-8 as
# Latin-1 — the classic ``í`` → ``Ã­`` mojibake. We strip every such meta
# and inject a canonical UTF-8 meta so both parsers agree on the encoding.
_CHARSET_META_RE = re.compile(
    r"""<meta\s+[^>]*?(?:
            charset\s*=\s*["']?[^"'>\s/]+
          | http-equiv\s*=\s*["']?content-type["']?[^>]*?content\s*=\s*["'][^"']*charset=[^"';]+
        )[^>]*>""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)

# Outlook templates wrap layout in MSO/IE conditional comments. Three
# concrete shapes show up in the wild and all three must be handled, because
# bleach's ``strip_comments=True`` would otherwise take chunks of the email
# layout with them, and a half-unwrapped comment leaves stray ``<!--`` /
# ``-->`` that derail the html5lib parser:
#
# 1. ``<!--[if mso | IE]>DESKTOP<![endif]-->`` — *downlevel-hidden*: content
#    visible only to Outlook/IE. For modern webmail we want DESKTOP to render
#    as regular markup (the alternative mobile fallback is usually a 4%-wide
#    placeholder).
# 2. ``<!--[if !mso]><!-- -->VISIBLE<!--<![endif]-->`` — *downlevel-revealed*
#    using the short form: VISIBLE is what every non-Outlook client shows.
#    The inner ``<!-- -->`` + ``<!--`` pair nullifies the outer comment for
#    standards-compliant parsers. We must strip the FULL marker including the
#    inner comment delimiters, otherwise an unpaired ``<!--`` survives.
# 3. ``<!--[if gte mso 9]><xml>…</xml><![endif]-->`` — *downlevel-hidden* XML
#    island (Office document settings). Unwrapping is safe because bleach
#    will strip ``<xml>`` / ``<o:…>`` tags downstream.
#
# The two alternatives below cover the three shapes: the first matches the
# downlevel-revealed form (captures only the inner VISIBLE content), the
# second matches the downlevel-hidden form.
_MSO_CONDITIONAL_RE = re.compile(
    r"""
    # Revealed opener matches both the standard long form
    # ``<!--[if …]><!-- -->`` (space between the inner ``<!--`` and ``-->``)
    # AND the compact short form ``<!--[if …]><!-->`` (MailChimp/HubSpot)
    # where the inner comment self-closes with a bare ``>``. Missing the
    # compact form previously let the hidden-alternative regex swallow
    # ``<!--[if !mso]>…<!--<![endif]-->`` templates and drop their payload.
    <!--\s*\[if[^\]]*\]>\s*<!--(?:\s*-->|>)
    (?P<revealed>.*?)                       # visible payload
    <!--\s*<!\[endif\]-->                   # revealed closer: <!--<![endif]-->
    |
    <!--\s*\[if[^\]]*\]>                    # hidden opener: <!--[if …]>
    (?P<hidden>.*?)                         # Outlook-only payload
    <!\[endif\]-->                          # hidden closer: <![endif]-->
    """,
    re.DOTALL | re.IGNORECASE | re.VERBOSE,
)

# ``<script>`` blocks must be removed with their inner text — bleach's
# ``strip=True`` would keep their contents, which the browser would then
# execute as raw JavaScript. The alternation ``</script\s*>|\Z`` also matches
# unterminated blocks that extend to EOF, so malformed input is still handled.
_SCRIPT_BLOCK_RE = re.compile(
    r"<script\b[^>]*>.*?(?:</script\s*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# ``<style>`` blocks are extracted for in-place CSS sanitisation before the
# rest of the pipeline runs. Same unterminated-block tolerance as above.
_STYLE_BLOCK_RE = re.compile(
    r"(?P<open><style\b[^>]*>)(?P<body>.*?)(?P<close></style\s*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Document-level wrappers are stripped after premailer so ``<style>`` blocks
# that lived in ``<head>`` become body-level fragments. Without this step,
# lxml's ``fragment_fromstring`` (used by geometry restoration) restructures
# ``<head>``/``<body>`` and drops the residual ``<style>`` block, losing the
# ``@media`` queries that mobile-first newsletters rely on.
_DOC_WRAPPER_RE = re.compile(
    r"</?(?:html|head|body)\b[^>]*>",
    re.IGNORECASE,
)

# Head-only constructs that MUST NOT remain at body-level after flattening.
# Leaving ``<title>`` would leak the subject text as visible body content
# (bleach strips the tag but keeps the inner text). Leaving ``<meta>``,
# ``<link>``, ``<base>``, ``<!DOCTYPE>`` or stray ``<xml>`` islands confuses
# lxml's fragment parser (head-only elements forced at body level reorganise
# the tree and drop surrounding content — Pencil.dev #36, Artlist #56,
# Eurofirms #473). Stripping them here gives geometry restoration and bleach
# a clean fragment to work with.
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_TITLE_BLOCK_RE = re.compile(
    r"<title\b[^>]*>.*?(?:</title\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_XML_BLOCK_RE = re.compile(
    r"<xml\b[^>]*>.*?(?:</xml\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_LINK_RE = re.compile(r"<link\b[^>]*/?>", re.IGNORECASE)
_META_RE = re.compile(r"<meta\b[^>]*/?>", re.IGNORECASE)
_BASE_RE = re.compile(r"<base\b[^>]*/?>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body\b([^>]*)>", re.IGNORECASE)
_BODY_STYLE_ATTR_RE = re.compile(
    r"""style\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)
_BODY_BGCOLOR_ATTR_RE = re.compile(
    r"""bgcolor\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)

# Premailer rewrites HTML attributes like ``width="600"`` into inline styles
# (``style="width: 600px"``) and drops the original attribute. Mirroring the
# values back onto the HTML attribute gives the layout two independent
# channels to survive the rest of the pipeline. Used by
# ``_mirror_geometry_to_attributes``.
_GEOMETRY_STYLE_RE: dict[str, re.Pattern[str]] = {
    "width": re.compile(r"(?:^|;)\s*width\s*:\s*(\d+)(?:\.\d+)?\s*px", re.IGNORECASE),
    "height": re.compile(r"(?:^|;)\s*height\s*:\s*(\d+)(?:\.\d+)?\s*px", re.IGNORECASE),
}

# Property values matching this expression are dropped from CSS (both inline
# styles and ``<style>`` blocks). Covers ``expression(…)`` (legacy IE code
# execution) and any scheme-based URL carrying JavaScript.
_UNSAFE_CSS_VALUE_RE = re.compile(
    r"(expression\s*\(|javascript\s*:|vbscript\s*:)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step 1 — charset meta normalisation
# ---------------------------------------------------------------------------


def _normalize_charset_meta(html: str) -> str:
    """Drop any declared charset meta; inject canonical UTF-8 only inside ``<head>``.

    When the input has no ``<head>``, we deliberately skip the injection:
    prepending a ``<meta>`` before a ``<!DOCTYPE>`` would push lxml/html5lib
    into quirks mode, and a Python ``str`` already arrives Unicode-decoded —
    downstream parsers default to UTF-8 in the absence of any declaration.
    """
    html = _CHARSET_META_RE.sub("", html)
    head_open = _HEAD_OPEN_RE.search(html)
    if head_open:
        return html[: head_open.end()] + '<meta charset="utf-8">' + html[head_open.end():]
    return html


# ---------------------------------------------------------------------------
# Step 2 — MSO conditional unwrap
# ---------------------------------------------------------------------------


_MSO_DISCARD_BYTES_THRESHOLD = 200
_MSO_BODY_TEXT_THRESHOLD = 50


def _visible_text_length(html: str) -> int:
    """Rough count of visible text after stripping tags, comments and whitespace."""
    no_comments = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    no_tags = re.sub(r"<[^>]+>", "", no_comments)
    return len(re.sub(r"\s+", "", no_tags))


def _unwrap_mso_conditionals(html: str) -> str:
    """Unwrap downlevel-revealed conditionals and discard Outlook-only blocks.

    Two conditional-comment shapes appear in email templates:
      * ``<!--[if !mso]><!-- -->X<!--<![endif]-->`` — downlevel-revealed.
        ``X`` is what non-Outlook clients should see. We keep ``X`` (it IS
        our content) and drop the wrapper.
      * ``<!--[if mso | IE]>X<![endif]-->`` (also ``[if gte mso 9]``, etc.) —
        downlevel-hidden. ``X`` is Outlook-only; for web clients, ``X`` must
        stay invisible. Since the pipeline renders inside a sandboxed iframe
        (a web client, not Outlook), we discard ``X`` entirely. Unwrapping
        it caused visible duplication whenever templates shipped both a
        desktop MSO variant AND a non-MSO variant (Medusa Festival #20,
        Santander Open Academy #73).

    When discarding hidden blocks leaves an empty body, we log a warning so
    legacy MSO-only newsletters (rare but possible) become observable instead
    of silently disappearing.
    """
    discarded_bytes = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal discarded_bytes
        if m.group("revealed") is not None:
            return m.group("revealed")
        hidden = m.group("hidden") or ""
        discarded_bytes += len(hidden)
        return ""

    result = _MSO_CONDITIONAL_RE.sub(_replace, html)
    if (
        discarded_bytes > _MSO_DISCARD_BYTES_THRESHOLD
        and _visible_text_length(result) < _MSO_BODY_TEXT_THRESHOLD
    ):
        logger.warning(
            "MSO-only email detected: discarded %d bytes of Outlook-hidden "
            "content but the remaining body has < %d visible chars; "
            "consider whether the template needs special handling",
            discarded_bytes, _MSO_BODY_TEXT_THRESHOLD,
        )
    return result


# ---------------------------------------------------------------------------
# Step 3 — sanitise <style> blocks (keeps @media, @supports, @font-face)
# ---------------------------------------------------------------------------


def _sanitize_css_declarations(style: Any) -> None:
    """Strip disallowed properties and unsafe values from a CSSStyleDeclaration.

    Mutates ``style`` in place. Properties whose name is outside the allowlist
    are removed entirely; allowed properties whose value contains
    ``expression(…)`` / ``javascript:`` / ``vbscript:`` are dropped too.
    """
    for name in [p.name for p in style.getProperties(all=True)]:
        if name.lower() not in _ALLOWED_CSS_PROPERTIES:
            style.removeProperty(name)
            continue
        value = style.getPropertyValue(name) or ""
        if _UNSAFE_CSS_VALUE_RE.search(value):
            style.removeProperty(name)


def _sanitize_css_rules(rules: Any) -> None:
    """Filter a ``CSSRuleList`` in place: drop unsafe at-rules, recurse into media.

    Each rule is evaluated inside its own ``try`` so a single unparseable or
    malformed rule cannot take the whole ``<style>`` block down (previously a
    broken rule would wipe ``.preheader{display:none}`` sitting next to it,
    causing the preheader bugs #5, #22, #51, #73, #84, #143, #315).

    The at-rule decision uses ``_ALLOWED_CSS_AT_RULES`` as the single source
    of truth: if you want to drop ``@media`` in the future, remove it from
    the constant — no code change needed here.
    """
    from cssutils.css import CSSRule  # lazy — transitive dep of premailer

    # Map each cssutils rule constant to its at-rule keyword for an
    # allowlist lookup. CSSSupportsRule isn't always exposed as a constant,
    # so we fall back to a class-name check below.
    nested_at_rules: dict[int, str] = {
        CSSRule.MEDIA_RULE: "media",
        CSSRule.FONT_FACE_RULE: "font-face",
    }

    to_remove: list[Any] = []
    for rule in list(rules):
        try:
            rule_type = getattr(rule, "type", None)
            if rule_type == CSSRule.STYLE_RULE:
                _sanitize_css_declarations(rule.style)
                continue
            at_rule = nested_at_rules.get(rule_type)
            if at_rule is not None:
                if at_rule not in _ALLOWED_CSS_AT_RULES:
                    to_remove.append(rule)
                    continue
                if rule_type == CSSRule.MEDIA_RULE:
                    _sanitize_css_rules(rule.cssRules)
                else:
                    _sanitize_css_declarations(rule.style)
                continue
            if (
                type(rule).__name__ == "CSSSupportsRule"
                and "supports" in _ALLOWED_CSS_AT_RULES
            ):
                _sanitize_css_rules(rule.cssRules)
                continue
            # import, namespace, charset, page, keyframes, unknown → drop.
            to_remove.append(rule)
        except Exception as exc:
            logger.debug(
                "dropping unparseable CSS rule (%s): %s",
                type(exc).__name__, exc,
            )
            to_remove.append(rule)

    for rule in to_remove:
        try:
            rules.remove(rule)
        except Exception:
            # cssutils occasionally rejects a removal if the rule has already
            # been detached — treat as a no-op.
            pass


def _sanitize_style_blocks(html: str) -> str:
    """Sanitise every ``<style>`` block in place with cssutils.

    Keeps the ``<style>`` tag with filtered content so ``@media`` queries and
    pseudo-class rules survive and render inside the iframe. If a block fails
    to parse it is replaced with an empty ``<style>`` tag rather than dropping
    the surrounding markup — the rest of the pipeline still runs.
    """
    if "<style" not in html.lower():
        return html

    try:
        import cssutils  # lazy — transitive dep of premailer
    except Exception as exc:  # pragma: no cover — cssutils ships with premailer
        logger.warning("cssutils unavailable; dropping <style> blocks (%s): %s",
                       type(exc).__name__, exc)
        return _STYLE_BLOCK_RE.sub("", html)

    # Silence cssutils' verbose parser warnings — real emails are messy.
    cssutils.log.setLevel(logging.CRITICAL)

    def _replace(match: re.Match[str]) -> str:
        body = match.group("body")
        open_tag = match.group("open")
        close_tag = match.group("close") or "</style>"
        if not body or not body.strip():
            return f"{open_tag}{close_tag}"
        try:
            sheet = cssutils.parseString(body, validate=False)
            _sanitize_css_rules(sheet.cssRules)
            cleaned = sheet.cssText.decode("utf-8") if isinstance(sheet.cssText, bytes) else sheet.cssText
        except Exception as exc:
            logger.warning(
                "style block sanitize failed (%s): %s — dropping block",
                type(exc).__name__, exc,
            )
            return f"{open_tag}{close_tag}"
        return f"{open_tag}{cleaned}{close_tag}"

    return _STYLE_BLOCK_RE.sub(_replace, html)


# ---------------------------------------------------------------------------
# Step 4 — inline CSS via premailer
# ---------------------------------------------------------------------------


def _inline_css_via_premailer(html: str) -> str:
    """Inline style rules into ``style=""`` attrs, keep residual ``<style>`` intact.

    ``allow_network=False`` is load-bearing: premailer's default downloads any
    ``<link rel="stylesheet" href="…">`` the sender put in the email (an SSRF +
    read-tracking vector — the fetch happens server-side, unguarded, with no
    timeout) and, with ``keep_style_tags=True``, plants the downloaded body
    VERBATIM as ``<style>`` text. When that body is an HTML page (Google Fonts
    specimen pages in the wild), its ``</style>`` closes the block on the next
    reparse and the rest leaks into the email as visible markup ("texto
    extraño" antes de las imágenes — Eurofirms). Real mail clients (Gmail,
    Outlook web) never load external stylesheets either, so disabling the
    network is also rendering parity. The untouched ``<link>`` tags are
    stripped later by ``_flatten_document_wrappers``.
    """
    try:
        from premailer import transform  # lazy — avoids startup cost
        return transform(
            html,
            keep_style_tags=True,
            remove_classes=False,
            cssutils_logging_level="CRITICAL",
            disable_validation=True,
            allow_network=False,
        )
    except Exception as exc:
        logger.warning("premailer failed (%s): %s", type(exc).__name__, exc)
        return html


# ---------------------------------------------------------------------------
# Step 5a — flatten document wrappers so <style> survives lxml/bleach
# ---------------------------------------------------------------------------


def _extract_body_background(body_attrs: str) -> str:
    """Read ``style`` + ``bgcolor`` from a ``<body …>`` tag and return a
    combined CSS declaration string (e.g. ``background-color:#fafafa;margin:0``).
    Returns an empty string when the body has nothing we need to preserve.
    """
    style_match = _BODY_STYLE_ATTR_RE.search(body_attrs)
    style_value = ""
    if style_match:
        style_value = (style_match.group(1) or style_match.group(2) or "").strip()
    bgcolor_match = _BODY_BGCOLOR_ATTR_RE.search(body_attrs)
    bgcolor_value = ""
    if bgcolor_match:
        bgcolor_value = (
            bgcolor_match.group(1)
            or bgcolor_match.group(2)
            or bgcolor_match.group(3)
            or ""
        ).strip()
    parts: list[str] = []
    if bgcolor_value:
        # Promote the legacy attribute to a style so the iframe's own white
        # background cannot cover it.
        parts.append(f"background-color: {bgcolor_value}")
    if style_value:
        parts.append(style_value.rstrip(";"))
    return "; ".join(parts)


def _flatten_document_wrappers(html: str) -> str:
    """Strip head-only tags and document wrappers so the fragment is safe for
    lxml's fragment parser and bleach. When the original ``<body>`` carried a
    ``style``/``bgcolor``, wrap the resulting content in a ``<div>`` so the
    background survives the iframe's own ``body{background:#fff}`` reset.
    """
    wrap_style = ""
    body_match = _BODY_OPEN_RE.search(html)
    if body_match:
        wrap_style = _extract_body_background(body_match.group(1))

    # 1. Remove head-only blocks with their content (title leaks subject text
    #    into the body; xml islands confuse lxml's fragment parser).
    html = _DOCTYPE_RE.sub("", html)
    html = _TITLE_BLOCK_RE.sub("", html)
    html = _XML_BLOCK_RE.sub("", html)
    # 2. Remove head-only void tags.
    html = _LINK_RE.sub("", html)
    html = _META_RE.sub("", html)
    html = _BASE_RE.sub("", html)
    # 3. Remove html/head/body structural tags (not their content).
    html = _DOC_WRAPPER_RE.sub("", html)

    # 4. Re-apply the body's background inside a wrapping div so the iframe's
    #    own body{background:#fff} does not override it (HubSpot Netcapital
    #    lavender background, #8 / #9). Use html.escape(quote=True) so a
    #    style mixing single AND double quotes (e.g. font-family lists) keeps
    #    the attribute well-formed; CSSSanitizer cleans the CSS later.
    if wrap_style:
        html = f'<div style="{html_escape(wrap_style, quote=True)}">{html}</div>'

    return html


# ---------------------------------------------------------------------------
# Step 5b — mirror geometry to HTML attributes
# ---------------------------------------------------------------------------


_CRITICAL_ELEMENT_RE = re.compile(
    r"<(?:img|td|th|table)\b", re.IGNORECASE,
)


def _count_critical_elements(html_before: str, tree: Any) -> tuple[int, int]:
    """Return ``(count in the raw HTML, count in the lxml-reparsed tree)`` of the
    critical layout elements (``img``/``td``/``th``/``table``).

    Shared by the two lxml reparse passes (geometry mirroring and remote-image
    rewriting). A post-reparse count far below the input count is the signal
    that libxml2 mis-interpreted the fragment and its tree must not be trusted.
    Compares element COUNTS, not byte lengths: lxml normalises entities
    (``&`` → ``&amp;``) and requotes attributes, so byte deltas false-positive
    on perfectly valid input.
    """
    before = len(_CRITICAL_ELEMENT_RE.findall(html_before))
    after = sum(1 for _ in tree.iter("img", "td", "th", "table"))
    return before, after


def _mirror_geometry_to_attributes(html: str) -> str:
    """Mirror ``width``/``height`` from inline styles to HTML attributes.

    Premailer rewrites ``<td width="500">`` into ``<td style="width:500px">``
    and drops the attribute. Re-asserting it as an attribute on
    ``<img>``/``<td>``/``<th>``/``<table>`` gives signature tables and inline
    logos a fallback dimension even if the style is stripped later.

    Defensive guard: count critical layout elements (``img``/``td``/``th``/
    ``table``) before and after the lxml reparse. If the post-reparse count
    is less than half the input count, lxml mis-interpreted the fragment and
    we return the original ``html`` unchanged. We deliberately do NOT
    compare byte lengths — lxml normalises entities (``&`` → ``&amp;``) and
    requotes attributes, so byte deltas trigger false positives on totally
    valid input.
    """
    try:
        from lxml import html as lxml_html  # lazy — transitive dep of premailer
    except Exception as exc:  # pragma: no cover — lxml ships with premailer
        logger.warning("lxml unavailable for geometry restoration (%s): %s",
                       type(exc).__name__, exc)
        return html
    try:
        tree = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception as exc:
        logger.warning("geometry restoration parse failed (%s): %s",
                       type(exc).__name__, exc)
        return html
    critical_before, critical_after = _count_critical_elements(html, tree)
    if critical_before > 0 and critical_after < critical_before / 2:
        logger.warning(
            "geometry restoration lost critical elements (%d → %d); "
            "returning input unchanged",
            critical_before, critical_after,
        )
        return html
    for element in tree.iter("img", "td", "th", "table"):
        style = element.get("style") or ""
        if not style:
            continue
        for attr, pattern in _GEOMETRY_STYLE_RE.items():
            if element.get(attr):
                continue
            match = pattern.search(style)
            if match:
                element.set(attr, match.group(1))
    # fragment_fromstring wraps the content in a synthetic <div>; unwrap it.
    inner = "".join(
        lxml_html.tostring(child, encoding="unicode", with_tail=True)
        for child in tree
    )
    if tree.text:
        inner = tree.text + inner
    return inner


# ---------------------------------------------------------------------------
# Step 6 — strip <script> blocks
# ---------------------------------------------------------------------------


def _strip_script_blocks(html: str) -> str:
    """Remove ``<script>`` blocks entirely (tag and inner contents)."""
    return _SCRIPT_BLOCK_RE.sub("", html)


# ---------------------------------------------------------------------------
# Step 7 — final bleach sanitization
# ---------------------------------------------------------------------------


# The viewer iframe is sandboxed with ``allow-popups
# allow-popups-to-escape-sandbox``: a clicked link opens a REAL tab, so every
# ``<a>`` must carry ``noopener`` (severs ``window.opener`` — reverse
# tabnabbing) + ``noreferrer``. ``cid:``/``data:`` are excluded from the href
# schemes because they are image-only protocols here (kept in
# ``_ALLOWED_PROTOCOLS`` for ``img src`` / ``td background``).
_INBOUND_LINK_TARGET = "_blank"
_INBOUND_LINK_REL = "noopener noreferrer"
_ALLOWED_A_HREF_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel"})


def _clean_with_bleach(html: str) -> str:
    """Final allowlist pass: tags, attributes, protocols, inline CSS, links.

    Uses a ``Cleaner`` with the shared link-hardening filter appended so the
    ``<a>`` rewrite happens in the same parse pass as the sanitisation.
    """
    from bleach.css_sanitizer import CSSSanitizer  # lazy — optional dep
    from bleach.sanitizer import Cleaner

    from api.services.html_link_hardening import build_link_hardening_filter

    css_sanitizer = CSSSanitizer(
        allowed_css_properties=_ALLOWED_CSS_PROPERTIES,
        allowed_svg_properties=frozenset(),
    )
    cleaner = Cleaner(
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        css_sanitizer=css_sanitizer,
        strip=True,
        filters=[
            build_link_hardening_filter(
                target=_INBOUND_LINK_TARGET,
                rel=_INBOUND_LINK_REL,
                allowed_href_schemes=_ALLOWED_A_HREF_SCHEMES,
            )
        ],
    )
    return cleaner.clean(html)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def prepare_email_html(html: str) -> str:
    """Transform provider HTML into browser-safe HTML for the iframe viewer.

    Runs the full pipeline (charset → MSO unwrap → <style> sanitize → CSS
    inline → geometry mirror → <script> strip → bleach). Each step is
    fail-soft: on any unexpected error the step logs a warning and returns the
    input unchanged, so bleach always sees well-formed content and no step can
    take down the endpoint.
    """
    if not html or html.isspace():
        return html
    html = _normalize_charset_meta(html)
    html = _unwrap_mso_conditionals(html)
    html = _sanitize_style_blocks(html)
    html = _inline_css_via_premailer(html)
    html = _flatten_document_wrappers(html)
    html = _mirror_geometry_to_attributes(html)
    html = _strip_script_blocks(html)
    return _clean_with_bleach(html)


# ---------------------------------------------------------------------------
# Remote-image rewriting (runs AFTER the pure pipeline; injected rewriter)
# ---------------------------------------------------------------------------

# Only ``http(s)`` targets are rewritten. ``cid:`` / ``data:`` / relative /
# fragment URLs are left untouched — the proxy is for remote images only.
_REMOTE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# A CSS ``url(...)`` target, quoted or not. ``[^'")]+`` stops at the closing
# quote or paren so it never over-runs the declaration.
_CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<q>['"]?)(?P<url>[^'")]+)(?P=q)\s*\)""",
    re.IGNORECASE,
)

# CSS properties whose ``url(...)`` is an IMAGE inside a ``<style>`` block.
# ``@font-face { src: url(…) }`` is deliberately excluded: a font is not an
# image and the proxy only serves ``image/*``, so a proxied font would break.
_IMAGE_CSS_URL_PROPERTIES: frozenset[str] = frozenset(
    {"background", "background-image", "list-style", "list-style-image"}
)

# Safety-net for the (rare) case the structured lxml pass raises: rewrite
# ``src=`` / ``background=`` attributes carrying a remote URL. Post-bleach,
# ``src`` only exists on ``<img>`` and ``background`` only on
# ``td``/``th``/``table`` (allowlist scoping), so a bare attribute regex is
# safe. The captured URL is HTML-escaped in the raw string, so it is
# unescaped before signing and the sentinel is re-escaped for the attribute.
_IMG_URL_ATTR_RE = re.compile(
    r"""(?P<pre>\b(?:src|background)\s*=\s*)(?P<q>["'])(?P<url>https?://[^"'<>]*)(?P=q)""",
    re.IGNORECASE,
)


def _rewrite_css_url_values(css_value: str, url_rewriter: Callable[[str], str]) -> str:
    """Rewrite every remote ``url(...)`` target in a raw CSS value string."""
    def _replace(match: re.Match[str]) -> str:
        raw = match.group("url").strip()
        if not _REMOTE_URL_RE.match(raw):
            return match.group(0)
        q = match.group("q")
        return f"url({q}{url_rewriter(raw)}{q})"

    return _CSS_URL_RE.sub(_replace, css_value)


def _rewrite_style_declarations_images(
    style: Any, url_rewriter: Callable[[str], str],
) -> None:
    """Rewrite remote ``url(...)`` in image-property declarations, in place."""
    for name in [p.name for p in style.getProperties(all=True)]:
        if name.lower() not in _IMAGE_CSS_URL_PROPERTIES:
            continue
        value = style.getPropertyValue(name) or ""
        if "http://" not in value and "https://" not in value:
            continue
        new_value = _rewrite_css_url_values(value, url_rewriter)
        if new_value != value:
            style.setProperty(name, new_value)


def _rewrite_style_rules_images(rules: Any, url_rewriter: Callable[[str], str]) -> None:
    """Recurse a ``CSSRuleList`` rewriting image ``url(...)``; skip ``@font-face``."""
    from cssutils.css import CSSRule  # lazy — transitive dep of premailer

    for rule in rules:
        rule_type = getattr(rule, "type", None)
        if rule_type == CSSRule.STYLE_RULE:
            _rewrite_style_declarations_images(rule.style, url_rewriter)
        elif rule_type == CSSRule.MEDIA_RULE:
            _rewrite_style_rules_images(rule.cssRules, url_rewriter)
        elif type(rule).__name__ == "CSSSupportsRule":
            _rewrite_style_rules_images(rule.cssRules, url_rewriter)
        # FONT_FACE_RULE / import / etc. are intentionally left untouched.


def _rewrite_style_block(css: str, url_rewriter: Callable[[str], str]) -> str:
    """Rewrite remote image ``url(...)`` inside a ``<style>`` block via cssutils.

    Unlike attributes (which lxml decodes on read), ``<style>`` is a rawtext
    element: bleach HTML-escapes its content (``&`` -> ``&amp;``) and lxml hands
    it back verbatim. So the URL sitting in a declaration here is HTML-escaped;
    it is unescaped before signing (or the proxy would fetch a ``?a=1&amp;b=2``
    URL) and the sentinel is emitted with a raw ``&`` — valid in a CSS ``url()``
    and correct whether the viewer iframe reads the block as rawtext or decodes
    it via ``srcdoc``.
    """
    def _unescaping_rewriter(url: str) -> str:
        return url_rewriter(html_unescape(url))

    try:
        import cssutils  # lazy — transitive dep of premailer

        cssutils.log.setLevel(logging.CRITICAL)
        sheet = cssutils.parseString(css, validate=False)
        _rewrite_style_rules_images(sheet.cssRules, _unescaping_rewriter)
        text = sheet.cssText
        return text.decode("utf-8") if isinstance(text, bytes) else text
    except Exception as exc:
        logger.warning(
            "style-block image rewrite failed (%s): %s — leaving block unchanged",
            type(exc).__name__, exc,
        )
        return css


def _rewrite_images_structured(html: str, url_rewriter: Callable[[str], str]) -> str:
    """Rewrite remote images via lxml: ``img@src`` / ``td|th|table@background`` /
    inline ``style`` ``url(...)`` / ``<style>`` image ``url(...)``.

    lxml decodes entities on read and re-encodes on write, so the ``&`` in a
    query string round-trips correctly per context (``&amp;`` in attributes,
    raw ``&`` in ``<style>`` rawtext) without any manual escaping here.
    """
    from lxml import html as lxml_html  # lazy — transitive dep of premailer

    tree = lxml_html.fragment_fromstring(html, create_parent="div")
    # Defensive guard (same shape as _mirror_geometry_to_attributes): if libxml2
    # mis-parsed the bleach-clean fragment and dropped most of its layout
    # elements, do NOT serialise the mangled tree — raise so rewrite_remote_images
    # falls back to the regex path, which rewrites the ORIGINAL string without
    # restructuring (no content lost). Without this, a mis-parse silently
    # persists a mutilated body into the immutable email_content cache.
    critical_before, critical_after = _count_critical_elements(html, tree)
    if critical_before > 0 and critical_after < critical_before / 2:
        raise ValueError(
            f"lxml reparse dropped critical elements during image rewrite "
            f"({critical_before} → {critical_after})"
        )
    for element in tree.iter():
        tag = element.tag
        if not isinstance(tag, str):
            continue
        tag = tag.lower()
        if tag == "img":
            _rewrite_url_attr(element, "src", url_rewriter)
        elif tag in ("td", "th", "table"):
            _rewrite_url_attr(element, "background", url_rewriter)
        if tag == "style":
            css = element.text
            if css and ("http://" in css or "https://" in css):
                element.text = _rewrite_style_block(css, url_rewriter)
        else:
            inline = element.get("style")
            if inline and ("http://" in inline or "https://" in inline):
                # Inline styles rewrite EVERY remote ``url(...)`` (no per-property
                # filter, unlike ``<style>`` blocks which skip ``@font-face src``).
                # This is safe because this pass runs AFTER bleach: the CSSSanitizer
                # already dropped every property outside ``_ALLOWED_CSS_PROPERTIES``,
                # and the only ``url()``-bearing survivors that matter inline are
                # image properties. ``src`` is allowlisted only for ``@font-face``
                # (which cannot exist inline), so a stray inline ``src:url()`` is a
                # visual no-op — over-rewriting it breaks nothing and never leaks.
                # INVARIANT: keep ``_ALLOWED_CSS_PROPERTIES`` free of any non-image
                # ``url()`` property that is meaningful inline, or this must filter.
                element.set("style", _rewrite_css_url_values(inline, url_rewriter))

    inner = "".join(
        lxml_html.tostring(child, encoding="unicode", with_tail=True) for child in tree
    )
    if tree.text:
        inner = tree.text + inner
    return inner


def _rewrite_url_attr(
    element: Any, attr: str, url_rewriter: Callable[[str], str],
) -> None:
    value = element.get(attr)
    if value and _REMOTE_URL_RE.match(value.strip()):
        element.set(attr, url_rewriter(value.strip()))


def _rewrite_image_attrs_regex(html: str, url_rewriter: Callable[[str], str]) -> str:
    """Regex fallback used only when the structured lxml pass raises or mis-parses.

    Rewrites ``src=`` / ``background=`` attributes AND every remote ``url(...)``
    in the raw string. Covering ``url(...)`` too is what keeps the fallback
    privacy-safe: the structured pass filters ``url(...)`` by image property, but
    here — a rare emergency path — leaking a raw remote URL is the worst outcome,
    so we deliberately over-rewrite (a stray ``@font-face`` url gets proxied and
    that font breaks) rather than let any remote URL survive uncached. The
    signer is idempotent, so an already-rewritten sentinel is not double-wrapped.
    """
    def _replace_attr(match: re.Match[str]) -> str:
        raw = html_unescape(match.group("url"))
        sentinel = html_escape(url_rewriter(raw), quote=True)
        return f'{match.group("pre")}{match.group("q")}{sentinel}{match.group("q")}'

    html = _IMG_URL_ATTR_RE.sub(_replace_attr, html)
    # Second pass: any remaining remote ``url(...)`` (inline styles / <style>
    # blocks the attribute regex does not reach). Fidelity of ``&`` escaping is
    # best-effort here — a mis-signed URL breaks the image but never leaks it.
    return _rewrite_css_url_values(html, url_rewriter)


def rewrite_remote_images(html: str, url_rewriter: Callable[[str], str]) -> str:
    """Rewrite remote image references to the signed proxy sentinel.

    Runs on the already-sanitised (post-bleach) fragment. ``url_rewriter``
    maps a raw remote URL to its sentinel (injected so this stays pure and
    testable). Idempotent when ``url_rewriter`` is (the sentinel prefix is
    itself ``https://`` so a naive re-run must not double-wrap). Fail-soft:
    if the structured lxml pass raises, a regex fallback still rewrites the
    ``src=`` / ``background=`` attributes (a pathological email may then leak
    a rare ``url(...)`` the fallback does not cover — accepted residual).
    """
    if not html or ("http://" not in html and "https://" not in html):
        return html
    try:
        return _rewrite_images_structured(html, url_rewriter)
    except Exception as exc:
        logger.warning(
            "structured remote-image rewrite failed (%s): %s — using regex fallback",
            type(exc).__name__, exc,
        )
        return _rewrite_image_attrs_regex(html, url_rewriter)
