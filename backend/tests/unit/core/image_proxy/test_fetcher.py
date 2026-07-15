"""
Unit tests for ``core.image_proxy.fetcher`` — the anti-SSRF remote-image
downloader.

Every external boundary is faked: ``socket.getaddrinfo`` (name resolution) and
``httpx.Client`` (the HTTP transport). No test ever touches the network. The
guard clauses (``_is_blocked_ip`` / ``_assert_public_url``) are exercised
directly because they carry the security-critical logic.
"""

from __future__ import annotations

import ipaddress
import socket
import threading

import httpx
import pytest

from core.image_proxy import (
    FetchedImage,
    ImageProxyBlocked,
    ImageProxyNotAnImage,
    ImageProxyUnfetchable,
    fetch_remote_image,
)
from core.image_proxy import fetcher


# ── module-state isolation ─────────────────────────────────────────
# ``fetch_remote_image`` now reuses a module-level pooled client (keep-alive).
# That client persists across calls, so the fake cached by the FIRST
# ``fetch_remote_image`` of one test would otherwise be reused by the next — its
# queued responses already drained — turning the whole file red even though no
# assertion changed. Reset ``_client`` to ``None`` directly, NOT via
# ``close_client()``: the ``_FakeClient`` has no ``.close()`` method, so
# ``close_client()`` would ``AttributeError`` on the fake.


@pytest.fixture(autouse=True)
def _reset_pooled_client():
    fetcher._client = None
    yield
    fetcher._client = None


# ── fakes ──────────────────────────────────────────────────────────


def _patch_resolution(monkeypatch, ip_or_map):
    """Patch ``getaddrinfo``. ``ip_or_map`` is a single public/private IP string,
    a list of IP strings (a host resolving to MULTIPLE addresses), a
    ``{host: ip|list}`` map (``None`` value → DNS failure), or ``None``
    (failure)."""

    def _fake(host, port, proto=0):
        ip = ip_or_map.get(host) if isinstance(ip_or_map, dict) else ip_or_map
        if ip is None:
            raise socket.gaierror("name resolution failed")
        ips = [ip] if isinstance(ip, str) else list(ip)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 0))
            for addr in ips
        ]

    monkeypatch.setattr(fetcher.socket, "getaddrinfo", _fake)


class _FakeStreamResponse:
    def __init__(self, *, status_code=200, headers=None, is_redirect=False, chunks=(b"IMG",)):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self._chunks = chunks

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeClient:
    """Stands in for ``httpx.Client``. ``stream`` yields the queued responses in
    order; a queued ``Exception`` is raised instead."""

    def __init__(self, queue):
        self._queue = list(queue)
        self.requested_urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def stream(self, method, url):
        self.requested_urls.append(url)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client(monkeypatch, queue):
    client = _FakeClient(queue)
    monkeypatch.setattr(fetcher.httpx, "Client", lambda **_kwargs: client)
    return client


class _ClosableFakeClient(_FakeClient):
    """A ``_FakeClient`` that also exposes ``.close()`` so ``close_client()`` can
    be exercised (the plain fake has none — see the module-state fixture)."""

    def __init__(self, queue):
        super().__init__(queue)
        self.closed = False

    def close(self):
        self.closed = True


# ── _is_blocked_ip (security-critical, tested in isolation) ─────────


@pytest.mark.parametrize(
    "ip, blocked",
    [
        ("8.8.8.8", False),                       # public v4
        ("2001:4860:4860::8888", False),          # public v6
        ("10.0.0.1", True),                       # private (RFC 1918)
        ("192.168.1.10", True),                   # private
        ("172.16.5.5", True),                     # private
        ("127.0.0.1", True),                      # loopback
        ("169.254.169.254", True),                # link-local + cloud metadata
        ("100.64.0.1", True),                     # CGNAT (RFC 6598)
        ("0.0.0.0", True),                        # unspecified
        ("224.0.0.1", True),                      # multicast
        ("::1", True),                            # loopback v6
        ("fd00::1", True),                        # unique-local v6
        ("::ffff:10.0.0.1", True),                # IPv4-mapped private
    ],
)
def test_is_blocked_ip(ip, blocked):
    assert fetcher._is_blocked_ip(ipaddress.ip_address(ip)) is blocked


# ── _assert_public_url ─────────────────────────────────────────────


def test_assert_public_url_rejects_non_http_scheme():
    with pytest.raises(ImageProxyBlocked):
        fetcher._assert_public_url("ftp://cdn.example.com/x.png")


def test_assert_public_url_rejects_empty_host():
    with pytest.raises(ImageProxyBlocked):
        fetcher._assert_public_url("http:///just-a-path")


def test_assert_public_url_dns_failure_is_unfetchable(monkeypatch):
    _patch_resolution(monkeypatch, None)
    with pytest.raises(ImageProxyUnfetchable):
        fetcher._assert_public_url("https://no-such-host.example.com/x.png")


def test_assert_public_url_private_ip_is_blocked(monkeypatch):
    _patch_resolution(monkeypatch, "10.0.0.5")
    with pytest.raises(ImageProxyBlocked):
        fetcher._assert_public_url("https://internal.example.com/x.png")


def test_assert_public_url_public_ip_passes(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    # No raise.
    fetcher._assert_public_url("https://cdn.example.com/x.png")


def test_assert_public_url_blocks_a_host_resolving_to_a_mixed_address_set(monkeypatch):
    # A host resolving to BOTH a public and a private IP must be blocked: the
    # reject-if-any loop over every resolved address is what defends against a
    # DNS answer that mixes a decoy public address with a private one
    # (DNS-rebinding-style). Narrowing the loop to a single element would pass
    # this suite while silently reopening the SSRF hole. The public IP is listed
    # first to prove the reject fires even after a public address is seen.
    _patch_resolution(monkeypatch, ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(ImageProxyBlocked):
        fetcher._assert_public_url("https://mixed.example.com/x.png")


# ── fetch_remote_image — happy paths ───────────────────────────────


def test_fetch_returns_image_bytes_and_content_type(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"PNG", b"DATA")),
    ])
    result = fetch_remote_image("https://cdn.example.com/logo.png")
    assert isinstance(result, FetchedImage)
    assert result.content_type == "image/png"
    assert result.data == b"PNGDATA"


def test_fetch_strips_content_type_parameters(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(headers={"content-type": "image/JPEG; charset=binary"}),
    ])
    result = fetch_remote_image("https://cdn.example.com/p.jpg")
    assert result.content_type == "image/jpeg"


def test_fetch_follows_a_valid_redirect_and_revalidates_each_hop(monkeypatch):
    _patch_resolution(monkeypatch, {"a.example.com": "93.184.216.34", "b.example.com": "93.184.216.35"})
    client = _patch_client(monkeypatch, [
        _FakeStreamResponse(is_redirect=True, headers={"location": "https://b.example.com/final.png"}),
        _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"OK",)),
    ])
    result = fetch_remote_image("https://a.example.com/start.png")
    assert result.data == b"OK"
    # Both hops were requested — the redirect target was followed.
    assert client.requested_urls == [
        "https://a.example.com/start.png",
        "https://b.example.com/final.png",
    ]


# ── fetch_remote_image — anti-SSRF + guard failures ────────────────


def test_fetch_blocks_a_redirect_to_a_private_ip(monkeypatch):
    # First hop resolves public; the redirect target resolves private and is
    # rejected on re-validation before its request is made.
    _patch_resolution(monkeypatch, {"a.example.com": "93.184.216.34", "internal": "10.0.0.9"})
    _patch_client(monkeypatch, [
        _FakeStreamResponse(is_redirect=True, headers={"location": "https://internal/secret.png"}),
    ])
    with pytest.raises(ImageProxyBlocked):
        fetch_remote_image("https://a.example.com/start.png")


def test_fetch_rejects_non_image_content_type(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(headers={"content-type": "text/html"}, chunks=(b"<html>",)),
    ])
    with pytest.raises(ImageProxyNotAnImage):
        fetch_remote_image("https://cdn.example.com/not-an-image")


def test_fetch_rejects_oversized_declared_content_length(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(headers={
            "content-type": "image/png",
            "content-length": str(fetcher._MAX_BYTES + 1),
        }),
    ])
    with pytest.raises(ImageProxyNotAnImage):
        fetch_remote_image("https://cdn.example.com/huge.png")


def test_fetch_rejects_body_exceeding_the_cap_while_streaming(monkeypatch):
    # No (or a lying) Content-Length — the streaming cap is what stops it.
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(
            headers={"content-type": "image/png"},
            chunks=(b"\0" * (fetcher._MAX_BYTES + 1),),
        ),
    ])
    with pytest.raises(ImageProxyNotAnImage):
        fetch_remote_image("https://cdn.example.com/streamed-huge.png")


def test_fetch_upstream_4xx_is_unfetchable(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [_FakeStreamResponse(status_code=404)])
    with pytest.raises(ImageProxyUnfetchable):
        fetch_remote_image("https://cdn.example.com/missing.png")


def test_fetch_redirect_without_location_is_unfetchable(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [_FakeStreamResponse(is_redirect=True, headers={})])
    with pytest.raises(ImageProxyUnfetchable):
        fetch_remote_image("https://cdn.example.com/x.png")


def test_fetch_exceeding_max_redirects_is_unfetchable(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    # Always redirect (to a public host) so the redirect budget is exhausted.
    redirects = [
        _FakeStreamResponse(is_redirect=True, headers={"location": "https://cdn.example.com/next.png"})
        for _ in range(fetcher._MAX_REDIRECTS + 1)
    ]
    _patch_client(monkeypatch, redirects)
    with pytest.raises(ImageProxyUnfetchable):
        fetch_remote_image("https://cdn.example.com/loop.png")


def test_fetch_transport_error_is_unfetchable(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [httpx.ConnectError("connection refused")])
    with pytest.raises(ImageProxyUnfetchable):
        fetch_remote_image("https://cdn.example.com/x.png")


def test_fetch_out_of_range_port_is_unfetchable(monkeypatch):
    # An out-of-range port makes ``urlsplit(...).port`` raise ``ValueError`` when
    # accessed inside ``_assert_public_url`` — untyped, and NOT a socket.gaierror
    # nor an httpx error — so it funnels through the generic ``except Exception``
    # guard (the ONLY branch none of the other tests reach) into
    # ImageProxyUnfetchable. The client is patched but never used: the ValueError
    # fires before getaddrinfo or any request.
    _patch_client(monkeypatch, [])
    with pytest.raises(ImageProxyUnfetchable):
        fetch_remote_image("https://cdn.example.com:99999/logo.png")


# ── concurrency gate (threadpool protection) ───────────────────────


def test_download_gate_is_a_bounded_semaphore_sized_to_the_constant():
    # The gate MUST be a BoundedSemaphore sized to _MAX_CONCURRENT_DOWNLOADS so
    # surplus concurrent downloads WAIT for a slot instead of each holding an
    # anyio threadpool worker (the endpoint is synchronous + rate-limit-exempt).
    # A regression to a plain int / wrong size silently reopens the
    # threadpool-exhaustion risk a heavy newsletter would trigger.
    assert isinstance(fetcher._DOWNLOAD_GATE, threading.BoundedSemaphore)
    assert fetcher._DOWNLOAD_GATE._value == fetcher._MAX_CONCURRENT_DOWNLOADS


def test_fetch_acquires_and_releases_the_download_gate(monkeypatch):
    # A successful fetch must leave the gate's permit count unchanged (acquired
    # for the download, released on the way out) — no leaked permit that would
    # shrink capacity over time.
    _patch_resolution(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, [
        _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"OK",)),
    ])
    before = fetcher._DOWNLOAD_GATE._value
    fetch_remote_image("https://cdn.example.com/logo.png")
    assert fetcher._DOWNLOAD_GATE._value == before


def test_fetch_releases_the_download_gate_on_error(monkeypatch):
    # The permit must be released even when the fetch fails, or repeated errors
    # would drain the gate and deadlock all future downloads.
    _patch_resolution(monkeypatch, "10.0.0.5")  # private → ImageProxyBlocked
    _patch_client(monkeypatch, [])
    before = fetcher._DOWNLOAD_GATE._value
    with pytest.raises(ImageProxyBlocked):
        fetch_remote_image("https://internal.example.com/x.png")
    assert fetcher._DOWNLOAD_GATE._value == before


# ── privacy: neutral request headers ───────────────────────────────


def test_request_headers_carry_no_cookies_or_referer():
    """The whole point of the proxy is that the sender only ever sees the
    backend — FIXED headers (never the end user's real UA), and nothing that
    could leak the end user's identity. The UA is browser-like on purpose:
    CDN bot protection (Vercel — ideabrowser logo) answers 429 to unknown
    UAs, and the image ``Accept`` unblocks content-negotiating CDNs."""
    assert set(fetcher._REQUEST_HEADERS) == {"User-Agent", "Accept"}
    assert fetcher._REQUEST_HEADERS["User-Agent"].startswith("Mozilla/5.0 ")
    assert fetcher._REQUEST_HEADERS["Accept"].startswith("image/")
    assert "Cookie" not in fetcher._REQUEST_HEADERS
    assert "Referer" not in fetcher._REQUEST_HEADERS
    assert "Authorization" not in fetcher._REQUEST_HEADERS


# ── shared pooled client (keep-alive) + close_client ────────────────


def test_get_client_creates_the_pooled_client_once(monkeypatch):
    # Lazy singleton: the module client is constructed on first use and reused
    # thereafter — the same instance, never a fresh one per call.
    created: list[object] = []

    def _factory(**_kwargs):
        client = _FakeClient([])
        created.append(client)
        return client

    monkeypatch.setattr(fetcher.httpx, "Client", _factory)
    first = fetcher._get_client()
    second = fetcher._get_client()
    assert first is second
    assert len(created) == 1


def test_pooled_client_carries_keepalive_limits_and_no_redirect_following(monkeypatch):
    # The pooled client MUST enable connection keep-alive (the whole point of
    # sharing it) and MUST NOT follow redirects (every hop is re-validated by
    # hand — delegating to httpx would skip the anti-SSRF re-check).
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeClient([])

    monkeypatch.setattr(fetcher.httpx, "Client", _factory)
    fetcher._get_client()
    assert captured["follow_redirects"] is False
    limits = captured["limits"]
    assert limits.max_keepalive_connections == 20
    assert limits.keepalive_expiry == 30.0


def test_fetch_reuses_a_single_pooled_client_across_calls(monkeypatch):
    # Two images (same host) must ride ONE pooled client so the second reuses the
    # live TCP+TLS connection — the pre-fix code built a fresh client per image.
    _patch_resolution(monkeypatch, "93.184.216.34")
    created: list[_FakeClient] = []
    fake = _FakeClient([
        _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"A",)),
        _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"B",)),
    ])

    def _factory(**_kwargs):
        created.append(fake)
        return fake

    monkeypatch.setattr(fetcher.httpx, "Client", _factory)
    first = fetch_remote_image("https://cdn.example.com/a.png")
    second = fetch_remote_image("https://cdn.example.com/b.png")
    assert first.data == b"A"
    assert second.data == b"B"
    # One construction for two images — the client was pooled, not recreated.
    assert len(created) == 1


def test_close_client_closes_and_drops_the_pooled_client(monkeypatch):
    _patch_resolution(monkeypatch, "93.184.216.34")
    created: list[_ClosableFakeClient] = []

    def _factory(**_kwargs):
        client = _ClosableFakeClient([
            _FakeStreamResponse(headers={"content-type": "image/png"}, chunks=(b"X",)),
        ])
        created.append(client)
        return client

    monkeypatch.setattr(fetcher.httpx, "Client", _factory)
    fetch_remote_image("https://cdn.example.com/a.png")
    assert fetcher._client is created[0]

    fetcher.close_client()
    assert created[0].closed is True
    assert fetcher._client is None

    # A subsequent fetch lazily recreates a fresh pooled client.
    fetch_remote_image("https://cdn.example.com/b.png")
    assert len(created) == 2
    assert fetcher._client is created[1]


def test_close_client_is_a_noop_when_no_pooled_client_exists():
    # Shutdown may run with no image ever proxied (``_client`` is None). The
    # lifespan calls this best-effort, but the guard lives here: it must not raise.
    assert fetcher._client is None
    fetcher.close_client()
    assert fetcher._client is None
