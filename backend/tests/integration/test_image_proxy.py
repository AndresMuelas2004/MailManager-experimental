"""
Integration tests for the remote-email-image proxy endpoint:

``GET /image-proxy?u=…&s=…``

Router → service → cache (real DB, per-test transaction) → anti-SSRF fetcher.
The fetcher (``core.image_proxy.fetch_remote_image``) is mocked at the service
boundary so no test ever touches the network — the real anti-SSRF resolution
logic is covered by ``tests/unit/core/image_proxy/test_fetcher.py``. The
endpoint is unauthenticated (the HMAC signature is the access gate), so no
session/manager setup is needed.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit

import psycopg2
import pytest

from api.services import image_proxy_service
from api.services.image_proxy_signing import build_proxy_sentinel_url
from core.image_proxy import FetchedImage, ImageProxyBlocked, ImageProxyNotAnImage


_REMOTE_URL = "https://cdn.example.com/newsletter/logo.png"


@pytest.fixture(autouse=True)
def _reset_touch_throttle():
    """The touch throttle is module-level state keyed by url_hash. Every test
    here shares ``_REMOTE_URL`` (same hash), so without a reset a bump in one
    test would suppress the bump the cache-hit test asserts. Clear it per test."""
    image_proxy_service.reset_touch_throttle()
    yield
    image_proxy_service.reset_touch_throttle()


def _valid_us(url: str = _REMOTE_URL) -> dict[str, str]:
    """Mint the ``{u, s}`` query pair from the production signer."""
    query = parse_qs(urlsplit(build_proxy_sentinel_url(url)).query)
    return {"u": query["u"][0], "s": query["s"][0]}


def _url_hash(url: str = _REMOTE_URL) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _seed_cache_row(cur, *, content_type: str, image_bytes: bytes, url: str = _REMOTE_URL) -> None:
    cur.execute(
        """
        INSERT INTO image_proxy_cache (url_hash, url, content_type, image_bytes)
        VALUES (%(h)s, %(u)s, %(ct)s, %(b)s)
        """,
        {"h": _url_hash(url), "u": url, "ct": content_type, "b": psycopg2.Binary(image_bytes)},
    )


# ── invalid signature → 403 ────────────────────────────────────────


def test_image_proxy_invalid_signature_returns_403(test_client_base):
    resp = test_client_base.get("/image-proxy", params={"u": "not*base64", "s": "deadbeef"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "image_proxy_forbidden"


def test_image_proxy_tampered_signature_returns_403(test_client_base):
    params = _valid_us()
    params["s"] = params["s"][:-1] + ("0" if params["s"][-1] != "0" else "1")
    resp = test_client_base.get("/image-proxy", params=params)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "image_proxy_forbidden"


# ── valid signature, cache miss → fetch + serve ────────────────────


def test_image_proxy_cache_miss_fetches_and_serves_with_hardened_headers(
    test_client_base, monkeypatch, isolated_db,
):
    monkeypatch.setattr(
        image_proxy_service, "fetch_remote_image",
        lambda url: FetchedImage(content_type="image/png", data=b"PNGBYTES"),
    )

    resp = test_client_base.get("/image-proxy", params=_valid_us())
    assert resp.status_code == 200
    assert resp.content == b"PNGBYTES"
    assert resp.headers["content-type"] == "image/png"
    # MIME sniffing is disabled and the signed URL is stable → immutable cache.
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in resp.headers["cache-control"]

    # The fetched image was persisted (cache-aside write).
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT content_type FROM image_proxy_cache WHERE url_hash = %s",
            (_url_hash(),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "image/png"


# ── cache hit → serve without re-fetching ──────────────────────────


def test_image_proxy_cache_hit_serves_without_fetching(
    test_client_base, monkeypatch, isolated_db,
):
    with isolated_db.cursor() as cur:
        _seed_cache_row(cur, content_type="image/gif", image_bytes=b"GIF89a-CACHED")

    def _must_not_fetch(_url):
        raise AssertionError("cache hit must not call the fetcher")

    monkeypatch.setattr(image_proxy_service, "fetch_remote_image", _must_not_fetch)

    resp = test_client_base.get("/image-proxy", params=_valid_us())
    assert resp.status_code == 200
    assert resp.content == b"GIF89a-CACHED"
    assert resp.headers["content-type"] == "image/gif"


# ── cache hit → sliding-TTL bump ───────────────────────────────────


def test_image_proxy_cache_hit_bumps_last_accessed(
    test_client_base, monkeypatch, isolated_db,
):
    """A cache HIT bumps ``last_accessed_at`` (sliding TTL) via the router's
    post-response BackgroundTask, so a frequently-served image never expires.
    ``fetched_at`` stays put — a serve is not a re-fetch."""
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO image_proxy_cache
                (url_hash, url, content_type, image_bytes, fetched_at, last_accessed_at)
            VALUES (%(h)s, %(u)s, 'image/gif', %(b)s,
                    now() - INTERVAL '10 days', now() - INTERVAL '10 days')
            """,
            {"h": _url_hash(), "u": _REMOTE_URL, "b": psycopg2.Binary(b"GIF89a-CACHED")},
        )

    def _must_not_fetch(_url):
        raise AssertionError("cache hit must not call the fetcher")

    monkeypatch.setattr(image_proxy_service, "fetch_remote_image", _must_not_fetch)

    resp = test_client_base.get("/image-proxy", params=_valid_us())
    assert resp.status_code == 200

    # The BackgroundTask ran after the response on the shared test connection:
    # last_accessed_at was bumped to ~now(), well ahead of the 10-day-old seed,
    # while fetched_at (the immutable anchor) is untouched. A broken/absent touch
    # would leave the two equal and fail this assertion.
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT last_accessed_at, fetched_at FROM image_proxy_cache WHERE url_hash = %s",
            (_url_hash(),),
        )
        last_accessed, fetched_at = cur.fetchone()
    assert last_accessed > fetched_at


# ── anti-SSRF block → 403 ──────────────────────────────────────────


def test_image_proxy_ssrf_blocked_target_returns_403(test_client_base, monkeypatch, isolated_db):
    def _blocked(_url):
        raise ImageProxyBlocked("resolves to a private address")

    monkeypatch.setattr(image_proxy_service, "fetch_remote_image", _blocked)

    resp = test_client_base.get("/image-proxy", params=_valid_us())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "image_proxy_blocked_target"


# ── upstream non-image → 502 ───────────────────────────────────────


def test_image_proxy_non_image_upstream_returns_502(test_client_base, monkeypatch, isolated_db):
    def _not_image(_url):
        raise ImageProxyNotAnImage("upstream returned text/html")

    monkeypatch.setattr(image_proxy_service, "fetch_remote_image", _not_image)

    resp = test_client_base.get("/image-proxy", params=_valid_us())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "image_proxy_upstream_error"
