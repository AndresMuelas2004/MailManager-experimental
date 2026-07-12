"""HMAC signing + sentinel URL for the remote-email-image proxy.

Single source of truth for the sentinel prefix and the signature. Used by the
inbound sanitiser's image rewrite (to mint signed sentinel URLs baked into the
cached HTML) and by the proxy endpoint (to verify them). Pure apart from
reading the signing key from the environment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

# Sentinel prefix baked into the cached HTML. ``.invalid`` (RFC 6761) NEVER
# resolves, so if the frontend fails to rewrite it the image simply breaks
# (fail-closed, no leak). LOCKSTEP with the frontend: ``frontend/src/lib/
# imageProxy.ts`` must swap EXACTLY this prefix for ``{apiBase}/image-proxy``.
SENTINEL_PREFIX = "https://mm-image-proxy.invalid/img"

_DEV_FALLBACK_KEY = "dev-insecure-image-proxy-key-change-in-prod"


def _signing_key() -> bytes:
    """The HMAC key. API-layer env var (``create_app`` loads ``.env``).

    Falls back to a fixed dev key so tests / dev do not break. PROD MUST set a
    strong, persistent ``IMAGE_PROXY_SIGNING_KEY`` — the signature is baked
    into the cached HTML, so a key change invalidates every previously-signed
    URL (broken images until the content is re-sanitised on a cache miss).
    """
    return (os.getenv("IMAGE_PROXY_SIGNING_KEY") or _DEV_FALLBACK_KEY).encode("utf-8")


def _sign(url: str) -> str:
    return hmac.new(_signing_key(), url.encode("utf-8"), hashlib.sha256).hexdigest()


def build_proxy_sentinel_url(original_url: str) -> str:
    """Rewrite a raw remote URL to its signed sentinel URL (the pipeline's
    ``url_rewriter``). Idempotent: an already-sentinel URL is returned as-is so
    a re-run of the rewrite never double-wraps it.
    """
    if original_url.startswith(SENTINEL_PREFIX):
        return original_url
    encoded = base64.urlsafe_b64encode(original_url.encode("utf-8")).decode("ascii")
    return f"{SENTINEL_PREFIX}?u={encoded}&s={_sign(original_url)}"


def verify_and_extract(u: str, s: str) -> str | None:
    """Verify the signature and return the original URL, or ``None`` if invalid.

    Fail-closed: a malformed ``u`` or a mismatched ``s`` yields ``None`` (the
    caller raises a 403). ``hmac.compare_digest`` is constant-time.
    """
    try:
        original = base64.urlsafe_b64decode(u.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    if not hmac.compare_digest(_sign(original), s):
        return None
    return original
