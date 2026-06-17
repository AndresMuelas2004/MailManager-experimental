"""
Shared helpers used across all routers.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, Request

from api import rate_limit
from api.errors.exceptions import RequestTooLarge, TooManyRequests
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


def _enforce_rate_limit(bucket: str, identity: str) -> None:
    retry_after = rate_limit.check(bucket, identity)
    if retry_after is not None:
        raise TooManyRequests(
            f"Rate limit exceeded for the '{bucket}' bucket.",
            {"scope": bucket, "retry_after": retry_after},
        )


def rate_limit_by_ip(bucket: str):
    """``Depends`` factory: throttle by client IP (login + global safety net).

    The flag is read per request (cheap ``os.getenv``) so tests can toggle it
    via ``monkeypatch.setenv`` without rebuilding the session-scoped app.
    """

    def _dep(request: Request) -> None:
        if not rate_limit.rate_limiting_enabled():
            return
        identity = rate_limit.client_ip(
            request.headers.get("x-forwarded-for"),
            request.client.host if request.client else None,
        )
        _enforce_rate_limit(bucket, identity)

    return _dep


def rate_limit_by_user(bucket: str):
    """``Depends`` factory: throttle by the authenticated user.

    Depends on ``require_session``; FastAPI caches it per request by the
    original callable, so it runs once even when the route also depends on it,
    and the integration override (``require_session`` → ``TEST_USER_ID``) is
    shared with this dependency.
    """

    def _dep(
        request: Request,
        user_id: str = Depends(require_session),
    ) -> None:
        if not rate_limit.rate_limiting_enabled():
            return
        _enforce_rate_limit(bucket, user_id)

    return _dep
