"""Tests espejo de ``core.email.helpers.imagenes_cid`` (cid: inline, normalizacion y referencias)."""

from __future__ import annotations

from core.email.helpers import find_referenced_cids, inline_cid_images, normalize_cid


# ── inline_cid_images ──────────────────────────────────────────────


class TestInlineCidImages:
    def test_replaces_double_quoted_src(self):
        html = '<img src="cid:logo@x">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert out == '<img src="data:image/png;base64,AAA">'

    def test_replaces_single_quoted_src(self):
        html = "<img src='cid:logo@x'>"
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out

    def test_replaces_with_angle_brackets(self):
        html = '<img src="cid:<logo@x>">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out

    def test_replaces_background_attr(self):
        html = '<td background="cid:bg@x">hi</td>'
        out = inline_cid_images(html, {"bg@x": "data:image/jpeg;base64,BBB"})
        assert 'background="data:image/jpeg;base64,BBB"' in out

    def test_leaves_unmapped_cid_alone(self):
        html = '<img src="cid:missing"><img src="cid:known">'
        out = inline_cid_images(html, {"known": "data:image/png;base64,ZZZ"})
        assert 'src="cid:missing"' in out
        assert 'src="data:image/png;base64,ZZZ"' in out

    def test_empty_html_returns_empty(self):
        assert inline_cid_images("", {"x": "data:image/png;base64,AAA"}) == ""

    def test_empty_map_returns_html_unchanged(self):
        html = '<img src="cid:x">'
        assert inline_cid_images(html, {}) == html

    def test_resolves_url_func_in_css_style(self):
        html = '<div style="background-image:url(cid:bg@x)">hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert "cid:bg@x" not in out
        assert 'url("data:image/png;base64,AAAA")' in out

    def test_url_func_soft_fallback_when_cid_unknown(self):
        html = '<div style="background-image:url(cid:unknown)">hi</div>'
        out = inline_cid_images(html, {"other": "data:image/png;base64,AAA"})
        assert "cid:unknown" in out

    def test_url_func_with_double_quotes(self):
        html = '<div style=\'background-image:url("cid:bg@x")\'>hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert 'url("data:image/png;base64,AAAA")' in out

    def test_url_func_with_angle_brackets_and_whitespace(self):
        html = '<div style="background-image: url( cid:<bg@x> )">hi</div>'
        out = inline_cid_images(html, {"bg@x": "data:image/png;base64,AAAA"})
        assert 'url("data:image/png;base64,AAAA")' in out

    def test_case_mismatched_reference_resolves_via_normalized_lookup(self):
        # The provider clients key ``cid_map`` by the ``normalize_cid`` form;
        # an HTML reference whose case differs from the header must still
        # resolve (real senders are not case-consistent).
        html = '<img src="cid:Logo@X">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out

    def test_percent_encoded_reference_resolves_via_normalized_lookup(self):
        html = '<img src="cid:logo%40x">'
        out = inline_cid_images(html, {"logo@x": "data:image/png;base64,AAA"})
        assert 'src="data:image/png;base64,AAA"' in out


# ── find_referenced_cids ───────────────────────────────────────────


class TestFindReferencedCids:

    def test_extracts_from_src_attribute(self):
        html = '<img src="cid:logo@x"><img src="cid:hero@y">'
        assert find_referenced_cids(html) == {"logo@x", "hero@y"}

    def test_extracts_from_background_attribute(self):
        html = '<td background="cid:bg@x">x</td>'
        assert "bg@x" in find_referenced_cids(html)

    def test_extracts_from_url_func_in_style(self):
        html = '<div style="background-image:url(cid:bg@x)">x</div>'
        assert "bg@x" in find_referenced_cids(html)

    def test_handles_angle_brackets_around_cid(self):
        html = '<img src="cid:<logo@x>">'
        assert "logo@x" in find_referenced_cids(html)

    def test_empty_html_returns_empty_set(self):
        assert find_referenced_cids("") == set()
        assert find_referenced_cids(None) == set()

    def test_ignores_non_cid_urls(self):
        html = '<img src="https://example.com/logo.png">'
        assert find_referenced_cids(html) == set()

    def test_same_cid_referenced_twice_collapses(self):
        # The same CID referenced via both ``src`` and a CSS ``url(...)``
        # collapses to a single set entry (D-13 classification dedup).
        html = '<img src="cid:x@y"><div style="background:url(cid:x@y)">z</div>'
        assert find_referenced_cids(html) == {"x@y"}

    def test_returns_normalized_lowercase_form(self):
        # Output is ``normalize_cid`` form: the caller normalises the
        # provider-side Content-ID before the membership check.
        html = '<img src="cid:Logo@X">'
        assert find_referenced_cids(html) == {"logo@x"}

    def test_percent_encoded_reference_is_decoded(self):
        html = '<img src="cid:image%40example">'
        assert find_referenced_cids(html) == {"image@example"}


# ── normalize_cid ──────────────────────────────────────────────────


class TestNormalizeCid:

    def test_strips_angle_brackets_and_whitespace(self):
        assert normalize_cid(" <Logo@X> ") == "logo@x"

    def test_lowercases(self):
        assert normalize_cid("IMAGE001") == "image001"

    def test_percent_decodes(self):
        assert normalize_cid("image%40example") == "image@example"

    def test_plain_value_is_idempotent(self):
        assert normalize_cid("logo@x") == "logo@x"

    def test_empty_and_none_return_empty(self):
        assert normalize_cid("") == ""
        assert normalize_cid(None) == ""
