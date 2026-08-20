"""Imagenes inline ``cid:``: normalizacion de Content-ID y sustitucion por data URLs."""

from __future__ import annotations

import re
import urllib.parse


def normalize_cid(value: str | None) -> str:
    """Canonical form of a Content-ID for cross-referencing header ↔ HTML.

    Providers write the header as ``<id>`` while HTML references it as
    ``cid:id`` — sometimes with different letter case or percent-encoding
    (``cid:image%40x`` for ``image@x``). RFC 2392 treats CIDs as
    case-sensitive, but real senders do not: matching on the normalised form
    (percent-decoded, angle brackets stripped, lowercased) is what keeps the
    inline image resolving instead of rendering a broken icon. Producers of
    ``cid_map`` keys and ``find_referenced_cids`` output must both use it.
    """
    if not value:
        return ""
    return urllib.parse.unquote(value).strip().strip("<>").strip().lower()


# The CID body is GREEDY and the closing ``>`` is required only when an opening
# ``<`` was actually matched (``(?(lt)>)`` conditional). With the previous
# non-greedy body plus an optional ``>``, an UNQUOTED reference
# (``<img src=cid:image001@host>``, still emitted by older clients) had nothing
# forcing it to expand: the backreference to an empty quote matched immediately
# and the CID collapsed to its first character. That made
# ``find_referenced_cids`` report a CID no attachment could match, so the inline
# part was demoted to a downloadable (D-13) and the image rendered broken.
# The conditional keeps the tag's own ``>`` outside the match, which a blanket
# greedy ``>?`` would have swallowed.
_CID_REF_PATTERN = re.compile(
    r"""(?P<attr>src|background)\s*=\s*(?P<quote>["']?)cid:(?P<lt><)?(?P<cid>[^"'>\s]+)(?(lt)>)(?P=quote)""",
    re.IGNORECASE,
)

# Matches ``url(cid:<id>)`` inside CSS values — e.g. ``style="background-image:url(cid:…)"``
# produced by premailer when it inlines CSS rules from <style> blocks.
_CID_URL_FUNC_PATTERN = re.compile(
    r"""url\(\s*(?P<quote>["']?)cid:<?(?P<cid>[^"'>)\s]+?)>?(?P=quote)\s*\)""",
    re.IGNORECASE,
)


def _cid_replacer(cid_map: dict[str, str], formatter):
    """Build a regex ``sub`` replacer that resolves ``cid:<id>`` references.

    ``formatter(match, data_url)`` receives the match and the resolved data URL
    and returns the replacement string. Unmapped CIDs fall through to the
    original match text (soft fallback — broken image beats lost email).

    Lookup is two-step: exact key first (legacy maps keyed by the provider's
    raw Content-ID keep working), then the :func:`normalize_cid` form (the
    key shape the provider clients persist), so a case- or percent-encoding
    mismatch between header and HTML still resolves.
    """
    def _replace(match: re.Match[str]) -> str:
        cid = match.group("cid").strip()
        data_url = cid_map.get(cid)
        if data_url is None:
            data_url = cid_map.get(normalize_cid(cid))
        if data_url is None:
            return match.group(0)
        return formatter(match, data_url)
    return _replace


def inline_cid_images(html: str, cid_map: dict[str, str]) -> str:
    """Replace ``cid:<id>`` references with data URLs from ``cid_map``.

    Handles two forms:
    - HTML attributes: ``src="cid:…"`` / ``background="cid:…"``.
    - CSS ``url(cid:…)`` inside ``style="…"`` (after premailer CSS inlining).

    Tolerant to single/double/no quotes and optional angle brackets around
    the CID. Unmapped CIDs are left untouched (soft fallback).
    """
    if not html or not cid_map:
        return html

    html = _CID_REF_PATTERN.sub(
        _cid_replacer(cid_map, lambda m, url: f'{m.group("attr")}="{url}"'),
        html,
    )
    html = _CID_URL_FUNC_PATTERN.sub(
        _cid_replacer(cid_map, lambda _m, url: f'url("{url}")'),
        html,
    )
    return html


def find_referenced_cids(html: str | None) -> set[str]:
    """Return the set of CIDs referenced in ``html``, in ``normalize_cid`` form.

    Inspects both the HTML attribute form (``src="cid:…"`` /
    ``background="cid:…"``) and the CSS ``url(cid:…)`` form (produced by
    premailer when inlining ``<style>`` rules). Used by D-13 to decide
    whether an inline-marked attachment is actually referenced by the
    body and therefore should remain inline; if no reference exists it
    is promoted to a downloadable attachment.

    Every entry is normalised via :func:`normalize_cid` — callers must
    normalise the provider-side Content-ID before the membership check.
    """
    if not html:
        return set()
    referenced: set[str] = set()
    for match in _CID_REF_PATTERN.finditer(html):
        cid = normalize_cid(match.group("cid"))
        if cid:
            referenced.add(cid)
    for match in _CID_URL_FUNC_PATTERN.finditer(html):
        cid = normalize_cid(match.group("cid"))
        if cid:
            referenced.add(cid)
    return referenced
