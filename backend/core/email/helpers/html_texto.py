"""Conversiones HTML <-> texto plano y construccion de las citas del composer."""

from __future__ import annotations

import html as _html_lib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Tags whose textual content must NOT leak into the quoted body.
# ``_html_to_text`` drops the entire subtree of these instead of
# emitting their inner text.
_TEXT_DROP_TAGS = frozenset({"script", "style", "head", "title", "meta", "link"})

# Tags that should produce a line break in the plain-text output.
# Approximates how a renderer would visually flow the document — close
# enough for quotation purposes.
_TEXT_BREAK_TAGS = frozenset(
    {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "hr",
        "blockquote",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
    }
)


def plain_text_to_html(text: str | None) -> str:
    """Convert a legacy plain-text body into a minimal HTML fragment.

    Shared by the draft round-trip parsers (``_parse_gmail_draft`` /
    ``_parse_outlook_draft``) and conceptually identical to migration
    0034 (which re-implements the same escape + ``nl2br`` rule inline so
    it stays self-contained). The output is what seeds the rich-text
    composer when a draft that predates the HTML body still exists at the
    provider as ``text/plain``.

    Rules (escape FIRST, then linebreaks — never the reverse, or the
    freshly inserted ``<br>`` would be re-escaped to ``&lt;br&gt;``):

    - HTML-escape ``&`` / ``<`` / ``>`` so the original text cannot
      smuggle markup. ``quote=False`` is sufficient: the text lands in
      element content, never inside an attribute.
    - Single ``\\n`` becomes ``<br>``; the whole thing is wrapped in a
      single ``<p>…</p>``.
    - Empty / whitespace-only input returns ``""`` (no empty ``<p>``),
      matching the migration's "leave it blank" behaviour.
    """
    if not text or not text.strip():
        return ""
    escaped = _html_lib.escape(text, quote=False)
    with_breaks = escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return f"<p>{with_breaks}</p>"


def flatten_html_document(html: str | None) -> str:
    """Strip ``<html>``/``<head>``/``<body>`` wrappers and head-only tags.

    Used by ``_parse_outlook_draft`` to unwrap the document Graph returns
    when a draft body is stored as ``contentType=HTML`` (Graph re-wraps
    the content in ``<html><head><meta …us-ascii></head><body>…</body>``
    and the result is NOT byte-for-byte the HTML we sent). We keep only
    the ``<body>`` content as a clean fragment to seed the composer.

    Lives in ``core`` (stdlib ``HTMLParser`` only) instead of reusing the
    ``api.services.email_html_pipeline`` flattener because ``core`` must
    not import from ``api`` (layer isolation). Drops ``<script>`` /
    ``<style>`` / ``<head>`` / ``<title>`` / ``<meta>`` / ``<link>``
    subtrees outright and emits the rest verbatim. Fail-soft: a parser
    error returns the input unchanged.
    """
    if not html or not html.strip():
        return ""

    from html.parser import HTMLParser

    # Tags whose entire subtree (tag + content) is discarded.
    drop_tags = frozenset({"script", "style", "head", "title", "meta", "link", "base"})
    # Structural wrappers whose tag is dropped but content is kept.
    unwrap_tags = frozenset({"html", "body"})

    void_tags = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    parts: list[str] = []
    skip_depth = 0

    def _format_attrs(attrs: list[tuple[str, str | None]]) -> str:
        rendered: list[str] = []
        for name, value in attrs:
            if value is None:
                rendered.append(f" {name}")
            else:
                rendered.append(f' {name}="{_html_lib.escape(value, quote=True)}"')
        return "".join(rendered)

    class _Flattener(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if skip_depth > 0:
                if lower in drop_tags and lower not in void_tags:
                    skip_depth += 1
                return
            if lower in drop_tags:
                if lower not in void_tags:
                    skip_depth += 1
                return
            if lower in unwrap_tags:
                return
            parts.append(f"<{tag}{_format_attrs(attrs)}>")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if skip_depth > 0:
                return
            if lower in drop_tags or lower in unwrap_tags:
                return
            parts.append(f"<{tag}{_format_attrs(attrs)}/>")

        def handle_endtag(self, tag: str) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if skip_depth > 0:
                if lower in drop_tags and lower not in void_tags:
                    skip_depth -= 1
                return
            if lower in drop_tags or lower in unwrap_tags:
                return
            parts.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            if skip_depth > 0:
                return
            parts.append(data)

        def handle_entityref(self, name: str) -> None:
            if skip_depth > 0:
                return
            parts.append(f"&{name};")

        def handle_charref(self, name: str) -> None:
            if skip_depth > 0:
                return
            parts.append(f"&#{name};")

    try:
        parser = _Flattener(convert_charrefs=False)
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover — defensive: parser bugs
        logger.warning("flatten_html_document parser failed (%s): %s", type(exc).__name__, exc)
        return html

    return "".join(parts).strip()


def _html_to_text(html: str | None, *, max_chars: int = 50_000) -> str:
    """Degrade an HTML body to plain text for the Reply / Forward quote.

    Intentionally simpler than the rendering pipeline
    (``api.services.email_html_pipeline``): that pipeline produces a
    sanitised HTML fragment suitable for an iframe; here we want a
    plain-text degradation suitable for a ``<textarea>``. Differences:

    - Scripts, styles and metadata subtrees are discarded outright (no
      ``<style>`` content leaking as visible characters).
    - Block-level tags emit a newline so the visual line breaks of the
      original survive into the quote. ``<br>`` collapses to a single
      newline; ``<p>`` / ``<div>`` / list items / table rows behave the
      same to keep the output readable without sucking in `lxml`'s
      smart-rendering layer.
    - HTML entities are decoded (``&amp;`` → ``&``).
    - Output is clipped to ``max_chars`` so a 1 MB newsletter cannot
      blow up the composer field.

    Implementation uses Python's stdlib :py:class:`html.parser.HTMLParser`
    so this module remains import-safe regardless of which optional
    HTML stack (``lxml``, ``beautifulsoup``) is available. The plan
    initially suggested lxml; stdlib produces an identical result for
    the simple needs of this helper and keeps the dependency surface
    minimal.

    ``html`` of ``None`` or empty string returns ``""`` (soft fallback —
    the caller may then build a header-only quote).
    """
    if not html:
        return ""

    from html.parser import HTMLParser

    chunks: list[str] = []
    skip_depth = 0

    class _Extractor(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                skip_depth += 1
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_endtag(self, tag: str) -> None:
            nonlocal skip_depth
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                if skip_depth > 0:
                    skip_depth -= 1
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            # Self-closing tags like ``<br/>``.
            if tag.lower() in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_data(self, data: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(data)

        def handle_entityref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&{name};"))

        def handle_charref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&#{name};"))

    try:
        parser = _Extractor(convert_charrefs=False)
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover — defensive: parser bugs
        # Soft fallback: a visible-but-ugly text is better than losing
        # the entire quote. Strip tags brute-force via regex.
        logger.warning("_html_to_text parser failed (%s): %s", type(exc).__name__, exc)
        stripped = re.sub(r"<[^>]+>", "", html)
        return _html_lib.unescape(stripped)[:max_chars]

    raw = "".join(chunks)
    raw = _html_lib.unescape(raw)
    # Collapse runs of blank lines to at most two so the quote stays
    # visually tight without losing intentional paragraph breaks.
    raw = re.sub(r"\n[ \t]+", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    raw = raw.strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + "\n[...truncado...]"
    return raw


_SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _format_quoted_header_date(received_at: datetime | None) -> str:
    """Format ``received_at`` like Gmail web's "El 23 de mayo de 2026 a las 14:32".

    Soft fallback to ``"el {iso}"`` if ``received_at`` is ``None`` or
    not a ``datetime`` — the quote stays readable instead of crashing
    on a corrupt row.
    """
    if not isinstance(received_at, datetime):
        return ""
    try:
        local = received_at
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone.utc)
        # Render in UTC for now — i18n is out of MVP scope (R-05).
        month = _SPANISH_MONTHS[local.month - 1] if 1 <= local.month <= 12 else str(local.month)
        return f"El {local.day} de {month} de {local.year} a las {local.hour:02d}:{local.minute:02d}"
    except Exception:  # pragma: no cover — defensive
        return f"El {received_at.isoformat()}"


def _quote_lines(text: str) -> str:
    """Prefix every line of ``text`` with ``"> "`` (Gmail-web style).

    Empty input returns ``""`` (no header without content). A trailing
    newline is preserved so the quote ends with a clean line break.
    """
    if not text:
        return ""
    out_lines = [f"> {line}" if line else ">" for line in text.split("\n")]
    return "\n".join(out_lines)


def build_quoted_body(
    original_body_html: str | None,
    original_body_text: str | None,
    *,
    from_name: str,
    from_email: str,
    received_at: datetime | None,
    action: Literal["reply", "reply_all", "forward"],
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    subject: str | None = None,
) -> str:
    """Build the plain-text body for a Reply / Reply All / Forward composer.

    Output shape (Reply / Reply All):

    ```
    <blank line>
    <blank line>
    El 23 de mayo de 2026 a las 14:32, Ana López <ana@example.com> escribió:

    > Texto original línea 1
    > Texto original línea 2
    ```

    Output shape (Forward — Gmail / Outlook web style):

    ```
    <blank line>
    <blank line>
    ---------- Mensaje reenviado ----------
    De: Ana López <ana@example.com>
    Fecha: El 23 de mayo de 2026 a las 14:32
    Asunto: <subject>
    Para: a@x, b@x
    Cc: c@x

    <body sin prefijo>
    ```

    The two leading blank lines are intentional: the composer cursor
    lands on the first line and the user types **above** the quote
    without pisarla. R-05 makes the body plain-text even when the
    original is HTML — see :py:func:`_html_to_text` for the degrader.
    """
    if original_body_text:
        body_text = (original_body_text or "").strip()
    else:
        body_text = _html_to_text(original_body_html)

    sender_display = (from_name or "").strip()
    sender_email = (from_email or "").strip()
    if sender_display and sender_email:
        sender = f"{sender_display} <{sender_email}>"
    else:
        sender = sender_display or sender_email or "(remitente desconocido)"

    date_line = _format_quoted_header_date(received_at)

    if action == "forward":
        parts: list[str] = ["", "", "---------- Mensaje reenviado ----------"]
        parts.append(f"De: {sender}")
        if date_line:
            parts.append(f"Fecha: {date_line}")
        if subject:
            parts.append(f"Asunto: {subject}")
        if to_recipients:
            parts.append(f"Para: {', '.join(to_recipients)}")
        if cc_recipients:
            parts.append(f"Cc: {', '.join(cc_recipients)}")
        parts.append("")
        if body_text:
            parts.append(body_text)
        return "\n".join(parts)

    # reply / reply_all
    header = f"{date_line}, {sender} escribió:" if date_line else f"{sender} escribió:"
    quoted = _quote_lines(body_text)
    if quoted:
        return f"\n\n{header}\n\n{quoted}"
    return f"\n\n{header}"


# Inline ``<blockquote>`` style for the HTML reply/forward quote. Every
# property here MUST also be allowed by the outbound sanitizer
# (``api.services.outbound_html_pipeline``) so the quote survives the
# sanitisation the body goes through on create/update/send. Keep this
# string and that allowlist in lockstep.
_HTML_QUOTE_BLOCKQUOTE_STYLE = (
    "margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex;color:#555;"
)


def build_quoted_body_html(
    original_body_html: str | None,
    original_body_text: str | None,
    *,
    from_name: str,
    from_email: str,
    received_at: datetime | None,
    action: Literal["reply", "reply_all", "forward"],
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    subject: str | None = None,
) -> str:
    """Build the **HTML** body for a Reply / Reply All / Forward composer.

    HTML counterpart of :py:func:`build_quoted_body`. The original message
    is degraded to plain text via :py:func:`_html_to_text` (same degrader,
    same 50k cap + ``[...truncado...]`` marker as the plain-text quote —
    the rich-text composer intentionally does NOT ingest arbitrary sender
    HTML) and then re-promoted to a fragment inside a ``<blockquote>`` so
    it reads as a visually distinct quote.

    Output shape (Reply / Reply All):

    ```html
    <p>El 23 de mayo de 2026 a las 14:32, Ana López &lt;ana@example.com&gt; escribió:</p>
    <blockquote style="…"><p>línea 1<br>línea 2</p></blockquote>
    ```

    Output shape (Forward):

    ```html
    <p>---------- Mensaje reenviado ----------<br>De: …<br>Fecha: …<br>Asunto: …<br>Para: …</p>
    <blockquote style="…"><p>…</p></blockquote>
    ```

    The user types **above** the quote; the composer seeds with this HTML.
    Every element/attribute here is within the outbound sanitizer's
    allowlist so re-sanitising it on persist is a no-op.
    """
    if original_body_text:
        degraded_text = (original_body_text or "").strip()
    else:
        degraded_text = _html_to_text(original_body_html)

    quoted_fragment = plain_text_to_html(degraded_text)
    blockquote = (
        f'<blockquote style="{_HTML_QUOTE_BLOCKQUOTE_STYLE}">{quoted_fragment}</blockquote>'
        if quoted_fragment
        else ""
    )

    sender_display = (from_name or "").strip()
    sender_email = (from_email or "").strip()
    if sender_display and sender_email:
        sender = f"{sender_display} <{sender_email}>"
    else:
        sender = sender_display or sender_email or "(remitente desconocido)"

    date_line = _format_quoted_header_date(received_at)

    if action == "forward":
        header_lines = ["---------- Mensaje reenviado ----------", f"De: {sender}"]
        if date_line:
            header_lines.append(f"Fecha: {date_line}")
        if subject:
            header_lines.append(f"Asunto: {subject}")
        if to_recipients:
            header_lines.append(f"Para: {', '.join(to_recipients)}")
        if cc_recipients:
            header_lines.append(f"Cc: {', '.join(cc_recipients)}")
        header_html = "<br>".join(_html_lib.escape(line, quote=False) for line in header_lines)
        return f"<p>{header_html}</p>{blockquote}"

    # reply / reply_all
    header_text = f"{date_line}, {sender} escribió:" if date_line else f"{sender} escribió:"
    header_html = _html_lib.escape(header_text, quote=False)
    return f"<p>{header_html}</p>{blockquote}"


# Tags that open/close a list context for the plain-text alternative
# numbering. Tracked on a stack so nested ``<ol>``/``<ul>`` keep their
# own counters.
_LIST_OPEN_TAGS = frozenset({"ul", "ol"})


def html_to_plain_text_alternative(html: str | None, *, max_chars: int = 1_000_000) -> str:
    """Derive a readable ``text/plain`` rendition of an HTML body.

    Feeds the ``text/plain`` leg of Gmail's ``multipart/alternative`` (and
    is a courtesy to recipients whose client cannot display HTML). Built
    on the same stdlib ``HTMLParser`` engine as :py:func:`_html_to_text`
    but enriched in three ways that the bare degrader lacks (it is used by
    the plain-text quote with different semantics and MUST stay
    untouched):

    - ``<a href="url">texto</a>`` → ``texto (url)`` when the text differs
      from the URL, else just the URL. (The bare degrader drops ``href``.)
    - ``<li>`` inside ``<ul>`` → ``- item``; inside ``<ol>`` → ``1. item``,
      ``2. item``, … with a per-list counter. (The bare degrader emits a
      plain line break.)
    - ``<blockquote>`` content → every line prefixed with ``> `` (RFC 3676
      quoting). (The bare degrader emits a line break with no prefix.)

    Unlike :py:func:`_html_to_text`, the default ``max_chars`` is ~1 MB
    (aligned with the schema body cap) and truncation is **silent** — no
    ``[...truncado...]`` marker, because this is the actual body a
    recipient reads, not a quote. In practice the cap is never hit because
    the body is already bounded at the API schema boundary.

    ``None`` / empty / whitespace-only input returns ``""``.
    """
    if not html or not html.strip():
        return ""

    from html.parser import HTMLParser

    # Sentinel bytes wrap blockquote regions so a post-pass can prefix the
    # lines inside them with ``> `` (RFC 3676). They are control chars that
    # never appear in real body text and are removed before returning.
    _BQ_OPEN = "\x00"
    _BQ_CLOSE = "\x01"

    chunks: list[str] = []
    skip_depth = 0
    # Stack of list contexts: each entry is a mutable [kind, counter]
    # where kind is "ul" / "ol". The innermost list governs the marker.
    list_stack: list[list[Any]] = []
    # ``href`` of the anchor currently open (None outside anchors). The
    # link text is captured between start/end so we can append " (url)".
    anchor_href: str | None = None
    anchor_text_start: int | None = None

    class _Extractor(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal skip_depth, anchor_href, anchor_text_start
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                skip_depth += 1
                return
            if skip_depth > 0:
                return
            if lower in _LIST_OPEN_TAGS:
                list_stack.append([lower, 0])
                chunks.append("\n")
                return
            if lower == "li":
                chunks.append("\n")
                if list_stack:
                    ctx = list_stack[-1]
                    if ctx[0] == "ol":
                        ctx[1] += 1
                        chunks.append(f"{ctx[1]}. ")
                    else:
                        chunks.append("- ")
                else:
                    chunks.append("- ")
                return
            if lower == "blockquote":
                chunks.append("\n")
                chunks.append(_BQ_OPEN)
                return
            if lower == "a":
                href = ""
                for name, value in attrs:
                    if name.lower() == "href" and value:
                        href = value.strip()
                        break
                anchor_href = href or None
                anchor_text_start = len(chunks)
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_endtag(self, tag: str) -> None:
            nonlocal skip_depth, anchor_href, anchor_text_start
            lower = tag.lower()
            if lower in _TEXT_DROP_TAGS:
                if skip_depth > 0:
                    skip_depth -= 1
                return
            if skip_depth > 0:
                return
            if lower in _LIST_OPEN_TAGS:
                if list_stack:
                    list_stack.pop()
                chunks.append("\n")
                return
            if lower == "li":
                return
            if lower == "blockquote":
                chunks.append(_BQ_CLOSE)
                chunks.append("\n")
                return
            if lower == "a":
                if anchor_href is not None and anchor_text_start is not None:
                    link_text = "".join(chunks[anchor_text_start:]).strip()
                    if link_text and link_text != anchor_href:
                        chunks.append(f" ({anchor_href})")
                    elif not link_text:
                        chunks.append(anchor_href)
                anchor_href = None
                anchor_text_start = None
                return
            if lower in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if skip_depth > 0:
                return
            if tag.lower() in _TEXT_BREAK_TAGS:
                chunks.append("\n")

        def handle_data(self, data: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(data)

        def handle_entityref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&{name};"))

        def handle_charref(self, name: str) -> None:
            if skip_depth > 0:
                return
            chunks.append(_html_lib.unescape(f"&#{name};"))

    try:
        parser = _Extractor(convert_charrefs=False)
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover — defensive: parser bugs
        logger.warning(
            "html_to_plain_text_alternative parser failed (%s): %s",
            type(exc).__name__, exc,
        )
        stripped = re.sub(r"<[^>]+>", "", html)
        return _html_lib.unescape(stripped)[:max_chars]

    raw = "".join(chunks)
    raw = _html_lib.unescape(raw)
    raw = _apply_blockquote_prefix(raw, _BQ_OPEN, _BQ_CLOSE)
    # Normalise whitespace: trim trailing spaces, drop leading indentation
    # on each line, and collapse runs of blank lines to at most two.
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    raw = re.sub(r"\n[ \t]+", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    raw = raw.strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip()
    return raw


def _apply_blockquote_prefix(text: str, open_marker: str, close_marker: str) -> str:
    """Prefix every line between ``open_marker`` / ``close_marker`` with ``"> "``.

    The markers are emitted by :py:func:`html_to_plain_text_alternative`
    around ``<blockquote>`` regions. Nesting increments the prefix depth
    so a doubly-quoted block becomes ``"> > "``. Markers are stripped from
    the output. Unbalanced markers (truncated HTML) are tolerated — the
    depth simply clamps at zero.
    """
    if open_marker not in text and close_marker not in text:
        return text
    out_lines: list[str] = []
    depth = 0
    for line in text.split("\n"):
        # Process markers in order within the line, tracking depth, and
        # strip them from the visible content.
        cleaned_chars: list[str] = []
        line_entry_depth = depth
        for ch in line:
            if ch == open_marker:
                depth += 1
            elif ch == close_marker:
                depth = max(0, depth - 1)
            else:
                cleaned_chars.append(ch)
        cleaned = "".join(cleaned_chars)
        # A line is quoted if it began inside a blockquote OR opened one
        # before any visible text. Use the max depth seen on the line as
        # the prefix level so the marker line itself is quoted too.
        prefix_depth = max(line_entry_depth, depth) if (line_entry_depth or depth) else 0
        if prefix_depth > 0 and cleaned.strip():
            out_lines.append(("> " * prefix_depth) + cleaned)
        else:
            out_lines.append(cleaned)
    return "\n".join(out_lines)
