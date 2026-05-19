"""
Attachments routers — download received-email attachments + admin purge.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from api.routers.routers_helpers import require_session
from api.schemas.attachment import PurgeResult
from api.services import attachments_service
from core.email.helpers import format_content_disposition


# Two routers share this module:
# - ``email_attachments_router`` lives under the per-mailbox surface so
#   ownership checks can leverage the existing mailbox guard pattern.
# - ``admin_router`` lives at ``/admin/...`` and is gated by an env-var
#   token (D-30) instead of a session cookie because it's an operator
#   tool, not a user-facing endpoint.
email_attachments_router = APIRouter(
    prefix="/mailboxes/{mailbox_id}",
    tags=["attachments"],
)


admin_router = APIRouter(prefix="/admin", tags=["attachments-admin"])


@email_attachments_router.get(
    "/accounts/{account_id}/emails/{provider_message_id}/attachments/{attachment_id}",
)
def download_email_attachment(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    attachment_id: str,
    user_id: str = Depends(require_session),
) -> StreamingResponse:
    """Stream an email attachment binary to the client (D-21).

    Cache-aside: serves from ``email_attachment_blobs`` when present,
    otherwise pulls from the provider and persists. The response
    headers force browser download (``Content-Disposition: attachment``)
    and disable MIME sniffing (``X-Content-Type-Options: nosniff``).

    A BackgroundTask refreshes ``last_accessed_at`` after the stream
    completes so the TTL purge (D-15) only acts on truly idle blobs.
    """
    chunks, mime_type, filename, size = attachments_service.download_email_attachment(
        mailbox_id=mailbox_id,
        account_id=account_id,
        provider_message_id=provider_message_id,
        attachment_id=attachment_id,
        user_id=user_id,
    )
    headers = {
        "Content-Disposition": format_content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-cache",
        "Content-Length": str(size),
    }
    background = BackgroundTask(
        attachments_service.touch_attachment_last_accessed, attachment_id,
    )
    return StreamingResponse(
        chunks,
        media_type=mime_type,
        headers=headers,
        background=background,
    )


@admin_router.post("/attachments/purge", response_model=PurgeResult)
def purge_expired_attachments(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> PurgeResult:
    """Purge attachment blobs whose TTL has expired (D-15, D-30).

    Auth model:
    - ``ATTACHMENTS_PURGE_TOKEN`` env var must be set on the server.
    - Caller passes the same value via ``X-Admin-Token``.
    - No env var → 503 ``purge_disabled`` (deploy not configured).
    - Wrong / missing header → 401 ``invalid_admin_token``.
    """
    return attachments_service.purge_expired_attachments(x_admin_token)
