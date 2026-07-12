"""Remote-email-image proxy sub-package (fetcher + anti-SSRF).

Public facade: external consumers import from here, never from the internal
modules. Sibling of ``core.email`` under the implicit ``core`` namespace
package; it borrows ``CoreError`` from ``core.email`` for its error base
(there is no top-level ``core/errors.py``).
"""
from __future__ import annotations

from core.image_proxy.errors import (
    ImageProxyBlocked,
    ImageProxyError,
    ImageProxyNotAnImage,
    ImageProxyUnfetchable,
)
from core.image_proxy.fetcher import FetchedImage, fetch_remote_image

__all__ = [
    "FetchedImage",
    "ImageProxyBlocked",
    "ImageProxyError",
    "ImageProxyNotAnImage",
    "ImageProxyUnfetchable",
    "fetch_remote_image",
]
