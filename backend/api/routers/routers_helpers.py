"""
Shared helpers used across all routers.
"""

from __future__ import annotations

from fastapi import Cookie, Request

from api.errors.exceptions import RequestTooLarge
from api.services import auth_service


# 30 MB hard cap on multipart uploads (§5.4 of the attachments spec).
# D-01 already limits a single attachment to 25 MB; the extra 5 MB cushion
# covers the multipart boundary / headers overhead. Anything beyond that
# is treated as an attempted DoS and rejected before the body is read.
_MULTIPART_GLOBAL_SIZE_LIMIT_BYTES = 30 * 1024 * 1024


def require_session(session_id: str | None = Cookie(default=None)) -> str:
    """
    Validate the session cookie and return the authenticated user_id.
    """
    return auth_service.validate_session(session_id)


def enforce_multipart_size_limit(request: Request) -> None:
    """Reject ``Content-Length > 30 MB`` before reading the body.

    Applied as a ``Depends`` to the multipart upload endpoint
    (``POST /drafts/{id}/attachments``). Saves Starlette from buffering
    abusive payloads into memory and gives the client a fast, predictable
    ``413 request_too_large`` envelope. Requests without a
    ``Content-Length`` header (chunked transfer encoding) fall through —
    Starlette enforces its own per-request memory cap downstream.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except (TypeError, ValueError):
        return
    if length > _MULTIPART_GLOBAL_SIZE_LIMIT_BYTES:
        raise RequestTooLarge(
            "Multipart upload exceeds the 30 MB global cap before request body read."
        )
