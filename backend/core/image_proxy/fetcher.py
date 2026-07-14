"""Anti-SSRF HTTP fetcher for the remote-email-image proxy.

Downloads a remote ``http(s)`` image after validating that every host it
touches — the original URL and every redirect hop — resolves exclusively to
public IP addresses. This is the only place in ``core`` that speaks raw HTTP
to arbitrary sender-controlled URLs, so the SSRF guard is deliberately strict.

Residual (documented, accepted for the MVP): the guard resolves-and-validates
the host, then hands the URL to httpx which re-resolves it to open the
connection. A DNS entry with a sub-second TTL flipping from a public IP (seen
during validation) to a private one (seen by httpx) could slip through this
TOCTOU window. Closing it fully requires pinning the connection to the
validated IP while preserving TLS SNI — proposed as a future hardening. Every
hop is (re)validated immediately before its request to keep the window minimal.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from core.image_proxy.errors import (
    ImageProxyBlocked,
    ImageProxyError,
    ImageProxyNotAnImage,
    ImageProxyUnfetchable,
)


@dataclass(frozen=True)
class FetchedImage:
    content_type: str
    data: bytes


_MAX_BYTES = 10 * 1024 * 1024        # 10 MB hard cap on the decoded image
_TIMEOUT_S = 10.0                    # per-phase timeout (connect / read / write / pool)
_MAX_REDIRECTS = 3
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_USER_AGENT = "MailManager-ImageProxy/1.0"

# Neutral request headers: a fixed UA, and deliberately NO cookies, NO
# credentials, NO Referer, and nothing that could leak the end user's IP —
# the whole point of the proxy is that the sender only ever sees the backend.
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}

# Networks blocked in addition to the ``ipaddress`` boolean properties below.
# ``is_link_local`` / ``is_private`` already cover the metadata addresses and
# unique-local v6, but listing them explicitly is defence-in-depth against a
# future refactor that loosens the property checks.
_EXTRA_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),        # CGNAT (RFC 6598)
    ipaddress.ip_network("169.254.169.254/32"),   # cloud metadata (IPv4)
    ipaddress.ip_network("fd00:ec2::254/128"),    # AWS IMDS (IPv6)
)


# ---------------------------------------------------------------------------
# Shared HTTP client (connection pool with keep-alive).
#
# The proxy endpoint is synchronous, so several anyio threadpool threads share
# this one client. ``httpx.Client`` is safe for concurrent use across threads —
# its connection pool does its own locking — so reusing a single module-level
# client lets images from the same CDN reuse a live TCP+TLS connection instead
# of paying a fresh handshake per image (the dominant cost in image-heavy
# newsletters). Created lazily under a lock (double-checked) and closed from the
# app lifespan on shutdown. ``follow_redirects=False`` stays baked in here: we
# revalidate every hop by hand, so redirects must never be delegated to httpx.
# ---------------------------------------------------------------------------
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """Return the shared pooled client, creating it lazily (thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=httpx.Timeout(_TIMEOUT_S),
                    follow_redirects=False,
                    headers=_REQUEST_HEADERS,
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=100,
                        keepalive_expiry=30.0,
                    ),
                )
    return _client


def close_client() -> None:
    """Close the shared client and drop it so the next fetch recreates one.

    Called from the app lifespan on shutdown (and by tests to isolate the
    module-level pool between cases). A dropped client is recreated lazily by
    the next :func:`fetch_remote_image`.
    """
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *ip* is anything other than a routable public address."""
    # Unwrap IPv4-mapped IPv6 (``::ffff:10.0.0.1``) so a private v4 hidden
    # behind a v6 representation is still caught.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    return any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED_NETWORKS)


def _assert_public_url(url: str) -> None:
    """Validate scheme + host, resolve the host, and reject any private IP.

    Raises :py:class:`ImageProxyBlocked` for a disallowed scheme / empty host
    or a host that resolves to a blocked IP; :py:class:`ImageProxyUnfetchable`
    when the host cannot be resolved at all.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ImageProxyBlocked(f"Image proxy rejected non-http(s) scheme: {parts.scheme!r}.")
    host = parts.hostname
    if not host:
        raise ImageProxyBlocked("Image proxy rejected a URL with an empty host.")

    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImageProxyUnfetchable(f"Image proxy could not resolve host: {host!r}.") from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise ImageProxyUnfetchable(f"Image proxy resolved no address for host: {host!r}.")
    for raw_ip in resolved:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            # A getaddrinfo result we cannot parse is treated as unsafe.
            raise ImageProxyBlocked(
                f"Image proxy could not parse resolved address: {raw_ip!r}."
            ) from exc
        if _is_blocked_ip(ip):
            raise ImageProxyBlocked(
                f"Image proxy blocked host {host!r} resolving to non-public address."
            )


def _read_capped_body(response: httpx.Response) -> bytes:
    """Stream the response body, aborting if it exceeds ``_MAX_BYTES``.

    ``Content-Length`` is enforced here rather than trusted — it may be
    absent or lie, and gzip transfer encoding means the decoded size can
    exceed the advertised one.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > _MAX_BYTES:
            raise ImageProxyNotAnImage("Image proxy upstream body exceeded the size cap.")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_remote_image(url: str) -> FetchedImage:
    """Fetch a remote image with anti-SSRF validation and a size/type guard.

    Follows redirects manually (httpx ``follow_redirects=False``) so every hop
    is revalidated. Returns the image bytes + its ``Content-Type`` on success;
    raises :py:class:`ImageProxyBlocked` / :py:class:`ImageProxyUnfetchable` /
    :py:class:`ImageProxyNotAnImage` otherwise.
    """
    current = url
    client = _get_client()
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            _assert_public_url(current)
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ImageProxyUnfetchable(
                            "Image proxy upstream sent a redirect without a Location header."
                        )
                    current = str(httpx.URL(current).join(location))
                    continue
                if response.status_code >= 400:
                    raise ImageProxyUnfetchable(
                        f"Image proxy upstream returned status {response.status_code}."
                    )
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if not content_type.startswith("image/"):
                    raise ImageProxyNotAnImage(
                        f"Image proxy upstream returned non-image Content-Type: {content_type!r}."
                    )
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_length: int | None = int(declared)
                    except ValueError:
                        declared_length = None  # unparseable header — the streaming cap still applies
                    if declared_length is not None and declared_length > _MAX_BYTES:
                        raise ImageProxyNotAnImage(
                            "Image proxy upstream declared an oversized Content-Length."
                        )
                data = _read_capped_body(response)
                return FetchedImage(content_type=content_type, data=data)
        except ImageProxyError:
            # Re-raise typed domain errors (SSRF block, non-image, upstream
            # failure) intact so the service maps each to its own HTTP status.
            raise
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ImageProxyUnfetchable(
                "Image proxy could not fetch the remote image from upstream."
            ) from exc
        except Exception as exc:
            # Anything untyped escaping the SSRF frontier (a malformed port
            # raising ValueError, a host failing IDNA raising UnicodeError,
            # a body-streaming error) must not leak out of core untyped.
            raise ImageProxyUnfetchable(
                f"Image proxy unexpected fetch error ({type(exc).__name__}): {exc}"
            ) from exc

    raise ImageProxyUnfetchable("Image proxy exceeded the maximum number of redirects.")
