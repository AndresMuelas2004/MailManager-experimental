"""Shared ``<a>`` hardening filter for the two HTML sanitization pipelines.

Both pipelines (:mod:`api.services.email_html_pipeline` — inbound viewer —
and :mod:`api.services.outbound_html_pipeline` — composer) must force
``target``/``rel`` on every surviving link, and the inbound one must also
drop ``href`` values whose scheme is allowed globally by bleach for other
attributes (``cid:`` / ``data:`` are legitimate on ``<img src>`` but have no
legitimate use on a link). The filter plugs into ``bleach.sanitizer.Cleaner``
via its ``filters`` parameter, so the hardening runs in the same parse pass
as the sanitisation itself (no second html5lib round trip).

The forced ``rel`` matters because the viewer iframe is sandboxed with
``allow-popups allow-popups-to-escape-sandbox``: a clicked link opens a real
tab whose ``window.opener`` would otherwise point back at the mail UI
(reverse tabnabbing). ``noopener`` severs that reference.
"""

from __future__ import annotations

import re

# Scheme prefix per RFC 3986: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":".
# A value that does not match is relative / fragment-only and carries no
# scheme to filter on.
_HREF_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _href_scheme(href: str) -> str | None:
    """Return the lowercased URI scheme of ``href``, or ``None`` if relative."""
    match = _HREF_SCHEME_RE.match(href)
    return match.group(1).lower() if match else None


def build_link_hardening_filter(
    *,
    target: str,
    rel: str,
    allowed_href_schemes: frozenset[str] | None = None,
):
    """Build an html5lib ``Filter`` class that hardens every ``<a>`` start tag.

    - Forces ``target`` and ``rel`` to the given values (overwriting any
      sender/composer-supplied ones).
    - When ``allowed_href_schemes`` is given, removes the ``href`` attribute
      if its scheme is outside the set. Scheme-less (relative / ``#fragment``)
      values are kept — they cannot navigate anywhere harmful.

    Runs AFTER bleach's ``BleachSanitizerFilter`` in the ``Cleaner`` pipeline,
    so it only ever sees already-sanitised tokens.
    """
    from bleach.html5lib_shim import Filter  # lazy — keeps import cost off startup

    class _LinkHardeningFilter(Filter):
        def __iter__(self):
            for token in Filter.__iter__(self):
                if (
                    token.get("type") in ("StartTag", "EmptyTag")
                    and token.get("name") == "a"
                ):
                    data = token.setdefault("data", {})
                    if allowed_href_schemes is not None:
                        href = data.get((None, "href"))
                        if href:
                            scheme = _href_scheme(href)
                            if scheme is not None and scheme not in allowed_href_schemes:
                                del data[(None, "href")]
                    data[(None, "target")] = target
                    data[(None, "rel")] = rel
                yield token

    return _LinkHardeningFilter
