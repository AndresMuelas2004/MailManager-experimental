"""
Image proxy routers — serve remote email images through the backend + admin purge.

Two routers share this module (mirroring ``attachments_routers``):
- ``image_proxy_router`` serves ``GET /image-proxy`` with NO session cookie.
  The viewer iframe is sandboxed without ``allow-same-origin`` (origin "null")
  and the request is a cross-site subresource, so the session cookie never
  travels — the HMAC signature is the access gate instead. It is also registered
  EXEMPT from the global per-IP rate limit (see ``app.py``): a newsletter can
  reference dozens of images, so one bucket per image would trip the limit; the
  signature (only URLs our sanitiser minted) + the anti-SSRF guard bound abuse.
- ``image_proxy_admin_router`` serves the env-var-gated TTL purge, WITH the
  global rate limit like the attachments admin router.
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from api.schemas.image_proxy import ImageProxyPurgeResult
from api.services import image_proxy_service


image_proxy_router = APIRouter(tags=["image-proxy"])

image_proxy_admin_router = APIRouter(prefix="/admin", tags=["image-proxy-admin"])


@image_proxy_router.get("/image-proxy")
def get_proxied_image(u: str, s: str) -> StreamingResponse:
    """Serve a remote email image through the backend proxy.

    ``u`` is the base64url of the original remote URL and ``s`` its HMAC
    signature. Cache-aside: serves from ``image_proxy_cache`` on a hit,
    otherwise fetches via the anti-SSRF fetcher and persists. The response
    disables MIME sniffing and is aggressively browser-cacheable (the signed
    URL is stable, so ``immutable`` is safe). A BackgroundTask refreshes the
    cache TTL after the stream completes.
    """
    content_type, chunks, url_hash = image_proxy_service.get_proxied_image(u, s)
    headers = {
        "X-Content-Type-Options": "nosniff",
        # 30 days; the signed URL is stable, so the response is immutable.
        "Cache-Control": "private, max-age=2592000, immutable",
    }
    background = BackgroundTask(
        image_proxy_service.touch_cache_last_accessed, url_hash,
    )
    return StreamingResponse(
        chunks,
        media_type=content_type,
        headers=headers,
        background=background,
    )


@image_proxy_admin_router.post("/image-proxy/purge", response_model=ImageProxyPurgeResult)
def purge_expired_images(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> ImageProxyPurgeResult:
    """Purge cached proxied images whose TTL has expired (manual, no scheduler).

    Auth model (mirrors ``/admin/attachments/purge``):
    - ``IMAGE_PROXY_PURGE_TOKEN`` env var must be set on the server.
    - Caller passes the same value via ``X-Admin-Token``.
    - No env var → 503 ``purge_disabled``; wrong / missing header → 401
      ``invalid_admin_token``.
    """
    return image_proxy_service.purge_expired_images(x_admin_token)
