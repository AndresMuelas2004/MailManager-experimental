"""
Service layer for the remote-email-image proxy (GET /image-proxy + admin purge).

Cache-aside: verify the HMAC signature, serve from the DB cache on a hit, else
fetch through the anti-SSRF ``core.image_proxy`` fetcher, persist, and serve.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Iterator

from cachetools import TTLCache

logger = logging.getLogger(__name__)

from core.image_proxy import (
    ImageProxyBlocked,
    ImageProxyNotAnImage,
    ImageProxyUnfetchable,
    fetch_remote_image,
)
from database import DatabaseError, image_proxy_cache_store

from api.errors.exceptions import (
    DatabaseQueryError,
    ImageProxyBlockedTarget,
    ImageProxyForbidden,
    ImageProxyUpstreamError,
    InvalidAdminToken,
    PurgeDisabled,
)
from api.schemas.image_proxy import ImageProxyPurgeResult
from api.services.image_proxy_signing import verify_and_extract
from api.services.services_helpers import translate_database_error


_STREAM_CHUNK_SIZE = 64 * 1024  # 64 KB chunks for StreamingResponse


def _stream_bytes(data: bytes) -> Iterator[bytes]:
    """Yield in-memory bytes in fixed-size chunks for StreamingResponse.

    The image is bounded (``_MAX_BYTES`` in the fetcher), so the whole blob
    already sits in memory; this generator just paces it back to the client.
    """
    for offset in range(0, len(data), _STREAM_CHUNK_SIZE):
        yield data[offset : offset + _STREAM_CHUNK_SIZE]


def get_proxied_image(u: str, s: str) -> tuple[str, Iterator[bytes], str]:
    """Verify + serve a proxied remote image (cache-aside).

    Returns ``(content_type, chunk_iterator, url_hash)`` so the router can
    build the StreamingResponse and a BackgroundTask that refreshes the cache
    TTL. The three concrete fetcher errors are caught and mapped by hand (NOT
    via ``translate_core_error``: its ``_CORE_TO_API_MAP`` catch-all would
    turn an unmapped ``CoreError`` into a generic 500 instead of the intended
    403/502).
    """
    original_url = verify_and_extract(u, s)
    if original_url is None:
        raise ImageProxyForbidden("Invalid or missing signature on image proxy request.")
    url_hash = hashlib.sha256(original_url.encode("utf-8")).hexdigest()

    # 1) DB cache (Services -> Database).
    try:
        cached = image_proxy_cache_store.get(url_hash)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected image proxy cache read error (%s): %s", type(exc).__name__, exc,
        )
        raise ImageProxyUpstreamError(
            "Failed to read the image proxy cache before fetching."
        ) from exc
    if cached is not None:
        return cached["content_type"], _stream_bytes(cached["image_bytes"]), url_hash

    # 2) Cache miss -> anti-SSRF fetcher in core.
    try:
        fetched = fetch_remote_image(original_url)
    except ImageProxyBlocked as exc:
        raise ImageProxyBlockedTarget(
            "Image proxy blocked the remote target by anti-SSRF policy."
        ) from exc
    except ImageProxyNotAnImage as exc:
        raise ImageProxyUpstreamError(
            "Image proxy upstream returned non-image or oversized content."
        ) from exc
    except ImageProxyUnfetchable as exc:
        raise ImageProxyUpstreamError(
            "Image proxy failed to fetch the remote image from upstream."
        ) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected image proxy fetch error (%s): %s", type(exc).__name__, exc,
        )
        raise ImageProxyUpstreamError(
            "Unexpected failure fetching the remote image in the image proxy."
        ) from exc

    # 3) Persist best-effort: serve the image even if the cache write fails.
    try:
        image_proxy_cache_store.upsert(
            url_hash, original_url, fetched.content_type, fetched.data,
        )
    except Exception as exc:
        logger.warning(
            "Image fetched but proxy-cache persist failed (%s): %s",
            type(exc).__name__, exc, exc_info=exc,
        )

    return fetched.content_type, _stream_bytes(fetched.data), url_hash


# ---------------------------------------------------------------------------
# Sliding-TTL touch throttle.
#
# Serving an image counts as an access that should bump ``last_accessed_at``,
# but two facts make a DB write per served image wasteful: a browser only
# re-requests an image after its own 30-day immutable cache expires, and a
# single newsletter open fires DOZENS of GET /image-proxy at once. Opening a
# pool connection per served image for a trivial TTL bump would hammer the
# shared pool (max 25, also used by the backfill worker) precisely under the
# load this feature invites. Since the purge TTL is 30 DAYS of inactivity,
# sub-daily precision on ``last_accessed_at`` is irrelevant — so we bump at
# most once per URL hash per ``_TOUCH_THROTTLE_TTL_S``. In-process only (single
# uvicorn worker MVP, same trade-off as ``api/rate_limit.py``); the DB stays the
# source of truth for the actual 30-day TTL. Reset between tests via
# ``reset_touch_throttle``.
# ---------------------------------------------------------------------------

_TOUCH_THROTTLE_TTL_S = 24 * 60 * 60
_touch_throttle: TTLCache = TTLCache(maxsize=100_000, ttl=_TOUCH_THROTTLE_TTL_S)
_touch_throttle_lock = threading.Lock()


def _should_touch(url_hash: str) -> bool:
    """True at most once per ``url_hash`` per ``_TOUCH_THROTTLE_TTL_S`` (thread-safe)."""
    with _touch_throttle_lock:
        if url_hash in _touch_throttle:
            return False
        _touch_throttle[url_hash] = True
        return True


def reset_touch_throttle() -> None:
    """Clear the in-memory touch throttle. Test-isolation hook only."""
    with _touch_throttle_lock:
        _touch_throttle.clear()


def touch_cache_last_accessed(url_hash: str) -> None:
    """BackgroundTask body: refresh the cache TTL after a successful serve.

    Best-effort — a TTL hint, not a correctness guarantee. Throttled in memory
    (``_should_touch``) so a burst of served images does not open one pool
    connection each for a trivial bump. Swallows and logs every error (with
    ``exc_info`` since this is the only observability point).
    """
    if not _should_touch(url_hash):
        return
    try:
        image_proxy_cache_store.touch_last_accessed(url_hash)
    except Exception as exc:
        logger.warning(
            "Failed to touch image proxy cache last_accessed for %s (%s): %s",
            url_hash, type(exc).__name__, exc, exc_info=exc,
        )


# ---------------------------------------------------------------------------
# Admin: manual purge of expired cached images (mirrors the attachment purge).
# ---------------------------------------------------------------------------


_PURGE_TOKEN_ENV_VAR = "IMAGE_PROXY_PURGE_TOKEN"


def purge_expired_images(provided_token: str | None) -> ImageProxyPurgeResult:
    """Purge ``image_proxy_cache`` rows past the 30-day TTL.

    Same three-state auth model as ``attachments_service.purge_expired_attachments``,
    with its own dedicated env var / messages (§7 uniqueness):

    - env var absent → :py:class:`PurgeDisabled` (HTTP 503).
    - header missing / wrong → :py:class:`InvalidAdminToken` (HTTP 401).
    - otherwise execute and return the stats.
    """
    expected = os.getenv(_PURGE_TOKEN_ENV_VAR)
    if not expected:
        raise PurgeDisabled(
            f"Image proxy purge endpoint disabled: {_PURGE_TOKEN_ENV_VAR} is not set."
        )
    if not provided_token or provided_token != expected:
        raise InvalidAdminToken(
            "Invalid X-Admin-Token header for /admin/image-proxy/purge."
        )
    try:
        purged_count, freed_bytes = image_proxy_cache_store.purge_expired()
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected image proxy purge error (%s): %s", type(exc).__name__, exc,
        )
        raise DatabaseQueryError(
            "Failed to purge the image proxy cache via the admin endpoint."
        ) from exc
    return ImageProxyPurgeResult(purged_count=purged_count, freed_bytes=freed_bytes)
