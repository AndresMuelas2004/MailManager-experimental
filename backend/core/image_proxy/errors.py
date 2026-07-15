"""Error hierarchy for the remote-image proxy fetcher.

These are :py:class:`core.email.CoreError` subclasses (an intentional
intra-core dependency ``core.image_proxy -> core.email``: ``CoreError`` is
defined in ``core/email/errors.py`` and there is no top-level ``core/errors.py``
to host a shared base). The API service layer catches each concrete type and
maps it to the right ``ApiError`` — see ``api.services.image_proxy_service``.
"""
from __future__ import annotations

from core.email import CoreError


class ImageProxyError(CoreError):
    """Base for every remote-image-proxy fetch failure."""

    code = "image_proxy_error"
    default_message = "Image proxy error."


class ImageProxyBlocked(ImageProxyError):
    """The remote target was blocked by the anti-SSRF policy.

    Raised for a disallowed scheme, an empty host, or a host that resolves
    (at any redirect hop) to a private / loopback / link-local / reserved /
    multicast / metadata / CGNAT address.
    """

    code = "image_proxy_blocked"
    default_message = "Remote image target blocked by anti-SSRF policy."


class ImageProxyUnfetchable(ImageProxyError):
    """The remote image could not be retrieved.

    Raised on a network / timeout / connection error, a DNS resolution
    failure, an upstream 4xx/5xx status, a redirect missing its ``Location``
    header, exhaustion of the redirect budget, or any otherwise-untyped error
    escaping the anti-SSRF fetch frontier.
    """

    code = "image_proxy_unfetchable"
    default_message = "Remote image could not be fetched from upstream."


class ImageProxyNotAnImage(ImageProxyError):
    """Upstream returned a non-image ``Content-Type`` or content exceeding
    the size cap (declared via ``Content-Length`` or observed while
    streaming)."""

    code = "image_proxy_not_an_image"
    default_message = "Upstream returned non-image or oversized content."
