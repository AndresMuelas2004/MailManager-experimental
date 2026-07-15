"""
Unit tests for ``email_html_pipeline.rewrite_remote_images``.

The rewriter runs on the already-sanitised (post-bleach) fragment and swaps
every remote ``http(s)`` image reference for whatever ``url_rewriter`` returns.
A double rewriter (``PROXY::<url>``) is injected so the tests stay pure and can
assert exactly which URLs reached the rewriter and in what (decoded) shape. The
four rewrite surfaces are: ``img@src``, ``td|th|table@background``, inline
``style`` ``url(...)`` and ``<style>`` image ``url(...)``.
"""

from __future__ import annotations

import re

import cssutils
import lxml.html
import pytest

import api.services.email_html_pipeline as pipeline
from api.services.email_html_pipeline import rewrite_remote_images
from api.services.image_proxy_signing import (
    SENTINEL_PREFIX,
    build_proxy_sentinel_url,
    verify_and_extract,
)


# Matches every minted sentinel in the output, tolerating both the raw ``&``
# (``<style>`` rawtext / CSS) and the ``&amp;`` (HTML attribute) separator.
_SENTINEL_RE = re.compile(
    re.escape(SENTINEL_PREFIX) + r"\?u=(?P<u>[^&\"'\s)]+)&(?:amp;)?s=(?P<s>[0-9a-f]+)"
)


def _recovered_urls(html: str) -> list[str | None]:
    """Recover the original URL behind every sentinel in ``html`` by verifying
    its HMAC round-trips. A ``None`` entry means a signature failed to validate
    (the escaping corrupted ``u`` or ``s``)."""
    return [
        verify_and_extract(m.group("u"), m.group("s"))
        for m in _SENTINEL_RE.finditer(html)
    ]


def _recording_rewriter():
    """Return ``(rewriter, calls)`` — ``rewriter`` records every URL it sees."""
    calls: list[str] = []

    def _rw(url: str) -> str:
        calls.append(url)
        return f"PROXY::{url}"

    return _rw, calls


# ── the four rewrite surfaces ──────────────────────────────────────


def test_rewrites_remote_img_src():
    rw, calls = _recording_rewriter()
    result = rewrite_remote_images('<img src="https://cdn.example.com/logo.png">', rw)
    assert 'src="PROXY::https://cdn.example.com/logo.png"' in result
    assert calls == ["https://cdn.example.com/logo.png"]


@pytest.mark.parametrize("tag", ["td", "th", "table"])
def test_rewrites_remote_background_attribute(tag):
    rw, calls = _recording_rewriter()
    html = f'<table><tr><{tag} background="https://cdn.example.com/bg.png">.</{tag}></tr></table>'
    result = rewrite_remote_images(html, rw)
    assert 'background="PROXY::https://cdn.example.com/bg.png"' in result
    assert "https://cdn.example.com/bg.png" in calls


def test_rewrites_inline_style_background_url():
    rw, calls = _recording_rewriter()
    html = '<div style="background-image:url(https://cdn.example.com/hero.png)">x</div>'
    result = rewrite_remote_images(html, rw)
    assert "PROXY::https://cdn.example.com/hero.png" in result
    assert calls == ["https://cdn.example.com/hero.png"]


def test_rewrites_style_block_image_url():
    rw, calls = _recording_rewriter()
    html = "<style>.hero{background:url(https://cdn.example.com/hero.png)}</style><div class='hero'>x</div>"
    result = rewrite_remote_images(html, rw)
    assert "PROXY::https://cdn.example.com/hero.png" in result
    assert calls == ["https://cdn.example.com/hero.png"]


# ── what must NOT be rewritten ─────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["cid:logo@x", "data:image/png;base64,AAAA", "/relative/path.png", "#fragment"],
)
def test_leaves_non_remote_img_src_untouched(url):
    rw, calls = _recording_rewriter()
    result = rewrite_remote_images(f'<img src="{url}">', rw)
    assert f'src="{url}"' in result
    assert calls == []


def test_leaves_font_face_src_untouched():
    """``@font-face { src: url(…) }`` is deliberately excluded — the proxy only
    serves ``image/*``, so a proxied font would break."""
    rw, calls = _recording_rewriter()
    html = (
        "<style>@font-face{font-family:X;src:url(https://cdn.example.com/font.woff2)}</style>"
    )
    result = rewrite_remote_images(html, rw)
    assert "PROXY::" not in result
    assert "https://cdn.example.com/font.woff2" in result
    assert calls == []


def test_fast_path_returns_input_unchanged_without_remote_urls():
    rw, calls = _recording_rewriter()
    html = '<p>hola</p><img src="cid:x"><a href="mailto:a@b.c">x</a>'
    assert rewrite_remote_images(html, rw) == html
    assert calls == []


# ── entity decoding + idempotency ──────────────────────────────────


def test_rewriter_receives_the_entity_decoded_url():
    """lxml decodes ``&amp;`` on read, so the rewriter sees a raw ``&`` in the
    query string; the serialised attribute is re-encoded to ``&amp;``."""
    rw, calls = _recording_rewriter()
    result = rewrite_remote_images('<img src="https://cdn.example.com/i?a=1&amp;b=2">', rw)
    assert calls == ["https://cdn.example.com/i?a=1&b=2"]
    assert "PROXY::https://cdn.example.com/i?a=1&amp;b=2" in result


def test_idempotent_with_the_real_signing_rewriter():
    """Re-running the rewrite with the production signer must not double-wrap:
    the sentinel prefix is itself ``https://…`` and the signer short-circuits it."""
    html = (
        '<img src="https://cdn.example.com/a.png">'
        '<table><tr><td background="https://cdn.example.com/b.png">.</td></tr></table>'
    )
    once = rewrite_remote_images(html, build_proxy_sentinel_url)
    twice = rewrite_remote_images(once, build_proxy_sentinel_url)
    assert twice == once
    assert SENTINEL_PREFIX in once


# ── fail-soft + regex fallback ─────────────────────────────────────


def test_broken_style_block_is_left_unchanged(monkeypatch):
    """A ``<style>`` cssutils cannot parse must not abort the rewrite: the block
    is left untouched (raw URL survives) while attribute rewriting still runs."""
    def _boom(*_a, **_k):
        raise ValueError("cssutils exploded")

    monkeypatch.setattr(cssutils, "parseString", _boom)
    rw, _calls = _recording_rewriter()
    html = (
        "<style>.h{background:url(https://cdn.example.com/bg.png)}</style>"
        '<img src="https://cdn.example.com/logo.png">'
    )
    result = rewrite_remote_images(html, rw)
    # The <style> URL is left raw (fail-soft), but the <img> attribute still rewrites.
    assert "url(https://cdn.example.com/bg.png)" in result
    assert 'src="PROXY::https://cdn.example.com/logo.png"' in result


def test_regex_fallback_rewrites_attributes_when_lxml_raises(monkeypatch):
    """If the structured lxml pass raises, the regex safety-net still rewrites
    ``src=`` / ``background=``."""
    def _boom(*_a, **_k):
        raise RuntimeError("lxml exploded")

    monkeypatch.setattr(lxml.html, "fragment_fromstring", _boom)
    rw, calls = _recording_rewriter()
    html = (
        '<img src="https://cdn.example.com/logo.png">'
        '<td background="https://cdn.example.com/bg.png"></td>'
    )
    result = rewrite_remote_images(html, rw)
    assert 'src="PROXY::https://cdn.example.com/logo.png"' in result
    assert 'background="PROXY::https://cdn.example.com/bg.png"' in result
    assert "https://cdn.example.com/logo.png" in calls


def test_regex_fallback_also_rewrites_css_url_so_it_never_leaks(monkeypatch):
    """The regex fallback must ALSO rewrite ``url(...)`` in inline styles /
    ``<style>``, not just ``src=``/``background=``. Privacy is the invariant:
    when the structured pass bails, a raw remote ``url()`` surviving into the
    cached body would be a leak. Better to over-rewrite than to leak."""
    def _boom(*_a, **_k):
        raise RuntimeError("lxml exploded")

    monkeypatch.setattr(lxml.html, "fragment_fromstring", _boom)
    rw, _calls = _recording_rewriter()
    html = (
        '<img src="https://cdn.example.com/logo.png">'
        '<div style="background:url(https://cdn.example.com/bg.png)">x</div>'
    )
    result = rewrite_remote_images(html, rw)
    assert 'src="PROXY::https://cdn.example.com/logo.png"' in result
    assert "PROXY::https://cdn.example.com/bg.png" in result
    # No raw remote URL survived the fallback.
    assert "url(https://cdn.example.com/bg.png)" not in result


def test_guard_bails_to_fallback_when_lxml_drops_critical_elements(monkeypatch):
    """If libxml2 mis-parses the bleach-clean fragment and drops most layout
    elements, the structured pass must NOT serialise the mangled tree — it bails
    to the regex fallback, which rewrites the original string without
    restructuring (no content lost, nothing leaked)."""
    # Force the critical-element count to collapse, simulating a mis-parse.
    monkeypatch.setattr(pipeline, "_count_critical_elements", lambda _html, _tree: (10, 0))
    rw, _calls = _recording_rewriter()
    html = (
        '<img src="https://cdn.example.com/logo.png">'
        '<div style="background:url(https://cdn.example.com/bg.png)">x</div>'
    )
    result = rewrite_remote_images(html, rw)
    # Fell back to regex: attribute AND url() rewritten, original content intact.
    assert 'src="PROXY::https://cdn.example.com/logo.png"' in result
    assert "PROXY::https://cdn.example.com/bg.png" in result
    assert "url(https://cdn.example.com/bg.png)" not in result
    assert ">x</div>" in result


# ── signature round-trips through every surface (query-string URLs) ────
# These pin the load-bearing property the "PROXY::" tests above cannot: after
# the real signer runs, the minted sentinel's HMAC must VALIDATE. The ``&`` of a
# query string is exactly where escaping is most fragile (``<style>`` rawtext vs
# HTML attribute), and query strings are the common case in newsletter images.


def test_signature_round_trips_for_query_string_url_in_img_src():
    url = "https://cdn.example.com/logo.png?w=600&h=400&v=2"
    result = rewrite_remote_images(f'<img src="{url}">', build_proxy_sentinel_url)
    assert _recovered_urls(result) == [url]


def test_signature_round_trips_for_query_string_url_in_inline_style():
    url = "https://cdn.example.com/hero.png?w=600&h=400"
    html = f'<div style="background-image:url({url})">x</div>'
    result = rewrite_remote_images(html, build_proxy_sentinel_url)
    assert _recovered_urls(result) == [url]


def test_signature_round_trips_for_query_string_url_in_style_block():
    url = "https://cdn.example.com/hero.png?w=600&h=400"
    html = (
        "<style>@media (min-width:600px){.h{background-image:url(" + url + ")}}</style>"
        "<div class='h'>x</div>"
    )
    result = rewrite_remote_images(html, build_proxy_sentinel_url)
    assert _recovered_urls(result) == [url]


def test_signature_round_trips_for_entity_encoded_query_string_in_attribute():
    """Post-bleach, the ``&`` in an attribute URL arrives as ``&amp;``. lxml
    decodes it on read, so the signer sees the raw ``&`` and the recovered URL
    must be the decoded one."""
    decoded = "https://cdn.example.com/i.png?a=1&b=2"
    html = '<img src="https://cdn.example.com/i.png?a=1&amp;b=2">'
    result = rewrite_remote_images(html, build_proxy_sentinel_url)
    assert _recovered_urls(result) == [decoded]
