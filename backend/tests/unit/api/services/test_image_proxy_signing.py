"""
Unit tests for ``api.services.image_proxy_signing``.

Pure apart from reading the HMAC key from the environment. Exercises the
sentinel round-trip (mint → verify), tamper rejection (fail-closed), the
idempotency guard that keeps a re-run from double-wrapping a sentinel, and the
dev-fallback key selection.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from api.services import image_proxy_signing as signing
from api.services.image_proxy_signing import (
    SENTINEL_PREFIX,
    build_proxy_sentinel_url,
    verify_and_extract,
)


def _split_sentinel(sentinel: str) -> tuple[str, str]:
    """Pull ``(u, s)`` out of a minted sentinel URL."""
    query = parse_qs(urlsplit(sentinel).query)
    return query["u"][0], query["s"][0]


# ── build_proxy_sentinel_url ───────────────────────────────────────


def test_build_produces_sentinel_prefix_with_u_and_s():
    sentinel = build_proxy_sentinel_url("https://cdn.example.com/a.png")
    assert sentinel.startswith(f"{SENTINEL_PREFIX}?")
    u, s = _split_sentinel(sentinel)
    assert u and s


def test_build_is_idempotent_on_an_already_sentinel_url():
    # The sentinel prefix is itself ``https://…`` so a naive re-run of the
    # rewrite would double-wrap it; the guard returns it unchanged instead.
    once = build_proxy_sentinel_url("https://cdn.example.com/a.png")
    twice = build_proxy_sentinel_url(once)
    assert twice == once


# ── verify_and_extract — round-trip + tamper ───────────────────────


def test_verify_returns_original_url_for_a_well_signed_sentinel():
    original = "https://cdn.example.com/path/img.png?a=1&b=2"
    u, s = _split_sentinel(build_proxy_sentinel_url(original))
    assert verify_and_extract(u, s) == original


def test_verify_rejects_a_tampered_signature():
    u, s = _split_sentinel(build_proxy_sentinel_url("https://cdn.example.com/a.png"))
    # Flip the last character of the signature — fail-closed → None.
    tampered = s[:-1] + ("0" if s[-1] != "0" else "1")
    assert verify_and_extract(u, tampered) is None


def test_verify_rejects_a_signature_for_a_different_url():
    _u_a, s_a = _split_sentinel(build_proxy_sentinel_url("https://a.example.com/a.png"))
    u_b, _s_b = _split_sentinel(build_proxy_sentinel_url("https://b.example.com/b.png"))
    # Pair B's payload with A's signature — mismatch → None.
    assert verify_and_extract(u_b, s_a) is None


def test_verify_rejects_a_malformed_base64_payload():
    # ``u`` is not valid base64url — the decode raises and is swallowed → None.
    assert verify_and_extract("not*valid*base64", "deadbeef") is None


# ── signing key selection ──────────────────────────────────────────


def test_signing_key_defaults_to_dev_fallback_when_env_unset(monkeypatch):
    monkeypatch.delenv("IMAGE_PROXY_SIGNING_KEY", raising=False)
    assert signing._signing_key() == signing._DEV_FALLBACK_KEY.encode("utf-8")


def test_signing_key_uses_env_var_when_set(monkeypatch):
    monkeypatch.setenv("IMAGE_PROXY_SIGNING_KEY", "a-strong-prod-secret")
    assert signing._signing_key() == b"a-strong-prod-secret"


def test_signature_changes_with_the_key(monkeypatch):
    original = "https://cdn.example.com/a.png"
    monkeypatch.setenv("IMAGE_PROXY_SIGNING_KEY", "key-one")
    _u1, s1 = _split_sentinel(build_proxy_sentinel_url(original))
    monkeypatch.setenv("IMAGE_PROXY_SIGNING_KEY", "key-two")
    _u2, s2 = _split_sentinel(build_proxy_sentinel_url(original))
    # A key rotation invalidates every previously-signed URL (broken images
    # until re-sanitised) — the signatures must differ for the same URL.
    assert s1 != s2
