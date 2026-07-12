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


# ── fakes ──────────────────────────────────────────────────────────


def _patch_resolution(monkeypatch, ip_or_map):
    """Patch ``getaddrinfo``. ``ip_or_map`` is a single public/private IP string,
    a ``{host: ip}`` map (``None`` value → DNS failure), or ``None`` (failure)."""

    def _fake(host, port, proto=0):
        ip = ip_or_map.get(host) if isinstance(ip_or_map, dict) else ip_or_map
        if ip is None:
            raise socket.gaierror("name resolution failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

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


# ── privacy: neutral request headers ───────────────────────────────


def test_request_headers_carry_no_cookies_or_referer():
    """The whole point of the proxy is that the sender only ever sees the
    backend — a fixed UA, and nothing that could leak the end user's identity."""
    assert fetcher._REQUEST_HEADERS == {"User-Agent": "MailManager-ImageProxy/1.0"}
    assert "Cookie" not in fetcher._REQUEST_HEADERS
    assert "Referer" not in fetcher._REQUEST_HEADERS
