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

import cssutils
import lxml.html
import pytest

from api.services.email_html_pipeline import rewrite_remote_images
from api.services.image_proxy_signing import SENTINEL_PREFIX, build_proxy_sentinel_url


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
    ``src=`` / ``background=`` (a rare ``<style>`` url is the accepted residual)."""
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
