"""
Email router for metadata sync and sending.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from api.routers.routers_helpers import require_session
from api.schemas.email import (
    ConversationOut,
    EmailContentOut,
    EmailPageOut,
    EmailSendRequest,
    FavoriteSyncResponse,
    FavoriteUpdateRequest,
    FavoriteUpdateResponse,
    MoveToTrashRequest,
    MoveToTrashResult,
    ReadStatusRequest,
    ReadStatusResponse,
    ReplyContextOut,
    SpamRequest,
    SpamResponse,
    SyncResultOut,
    TrashActionRequest,
    TrashActionResult,
)
from api.services import emails_service


router = APIRouter(prefix="/mailboxes/{mailbox_id}/emails", tags=["emails"])


@router.get("", response_model=EmailPageOut)
def list_emails(
    mailbox_id: str,
    box: Literal["ALL_MAIL", "SENT", "SPAM", "TRASH"] = Query(...),
    account_id: str | None = Query(default=None),
    q: str | None = Query(
        default=None,
        min_length=2,
        max_length=200,
        description=(
            "Search query. Free text matches subject/sender as a substring "
            "(accent/case-insensitive). Also supports Gmail-style operators "
            "combined with AND: from:, to:, subject:, has:attachment, "
            "before:/after: (AAAA/MM/DD or AAAA-MM-DD, Europe/Madrid), "
            "is:read|unread|favorite (alias is:starred), in:inbox|sent|spam|trash. "
            "Quote phrases with double quotes. Unknown operators are treated as "
            "literal text; unsupported operator values are ignored. Whitespace-only "
            "is treated as no search; free-text tokens and operator clauses are each "
            "silently capped at 10. in: overrides the box shown for this listing."
        ),
    ),
    favorite: bool | None = Query(
        default=None,
        description=(
            "When true, restrict the listing to favourite emails and exclude "
            "TRASH/SPAM by default (unless box explicitly selects one of them)."
        ),
    ),
    group_by_thread: bool = Query(
        default=False,
        description=(
            "When true, collapse each conversation (thread) into one row "
            "(conversation view): the row represents the thread's most-recent "
            "message, its is_read/has_attachments/is_favorite are aggregated "
            "across the thread, thread_message_count is the number of messages "
            "of the thread in this box, and total counts threads. Favourites "
            "listings pass false (favourites are not grouped)."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(require_session),
) -> EmailPageOut:
    """
    List a page of email metadata for a mailbox, filtered by box.

    Returns an ``EmailPageOut`` envelope (``items`` + exact ``total`` of
    the filtered set + applied ``limit`` / ``offset``) so the client can
    render numbered pagination. Optionally filter to a single account,
    by free text, or by favourite status, or collapse threads into
    conversation rows via ``group_by_thread``.
    """
    return emails_service.list_emails(
        mailbox_id, box, user_id, account_id, q, limit, offset, favorite,
        group_by_thread,
    )


@router.post("/sync-metadata", response_model=SyncResultOut)
def sync_email_metadata(
    mailbox_id: str,
    account_id: str | None = Query(default=None),
    user_id: str = Depends(require_session),
) -> SyncResultOut:
    """
    Fetch and persist email metadata for a mailbox.
    Optionally sync only a single account.
    """
    return emails_service.sync_email_metadata(mailbox_id, user_id, account_id)


@router.post("/send")
def send_email(
    mailbox_id: str,
    payload: EmailSendRequest,
    user_id: str = Depends(require_session),
) -> dict[str, str]:
    """
    Send an email using a specific account under the mailbox.
    """
    return emails_service.send_email(mailbox_id, payload, user_id)


@router.post("/trash", response_model=TrashActionResult)
def manage_trash(
    mailbox_id: str,
    payload: TrashActionRequest,
    user_id: str = Depends(require_session),
) -> TrashActionResult:
    """
    Delete permanently or restore emails from trash.
    """
    return emails_service.manage_trash(mailbox_id, payload, user_id)


@router.post("/move-to-trash", response_model=MoveToTrashResult)
def move_to_trash(
    mailbox_id: str,
    payload: MoveToTrashRequest,
    user_id: str = Depends(require_session),
) -> MoveToTrashResult:
    """
    Move emails to trash.
    """
    return emails_service.move_to_trash(mailbox_id, payload, user_id)


@router.patch("/read-status", response_model=ReadStatusResponse)
def update_read_status(
    mailbox_id: str,
    payload: ReadStatusRequest,
    user_id: str = Depends(require_session),
) -> ReadStatusResponse:
    """
    Mark emails as read or unread across accounts in a mailbox.
    """
    return emails_service.update_read_status(mailbox_id, payload, user_id)


@router.post("/spam", response_model=SpamResponse)
def move_to_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str = Depends(require_session),
) -> SpamResponse:
    """
    Move emails to spam across accounts in a mailbox.
    """
    return emails_service.move_to_spam(mailbox_id, payload, user_id)


@router.post("/restore-from-spam", response_model=SpamResponse)
def restore_from_spam(
    mailbox_id: str,
    payload: SpamRequest,
    user_id: str = Depends(require_session),
) -> SpamResponse:
    """
    Restore emails from spam across accounts in a mailbox.
    """
    return emails_service.restore_from_spam(mailbox_id, payload, user_id)


@router.get(
    "/{provider_message_id}/content",
    response_model=EmailContentOut,
)
def get_email_full_content(
    mailbox_id: str,
    provider_message_id: str,
    account_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_session),
) -> EmailContentOut:
    return emails_service.get_email_full_content(
        mailbox_id, provider_message_id, account_id, user_id,
    )


# ---------------------------------------------------------------------------
# Favourites (Gmail STARRED label / Outlook flag).
# ---------------------------------------------------------------------------


# The favourites endpoints live under a separate router because they
# mount on a *different* path shape than the main /emails surface
# (``/mailboxes/{mid}/accounts/{aid}/emails/{mid}/favorite`` vs the
# regular ``/mailboxes/{mid}/emails``). Keeping a single FastAPI
# ``APIRouter(prefix=...)`` instance would force one of the two
# layouts; splitting routers keeps both URLs natural.
favorites_router = APIRouter(prefix="/mailboxes/{mailbox_id}", tags=["favorites"])


@favorites_router.patch(
    "/accounts/{account_id}/emails/{provider_message_id}/favorite",
    response_model=FavoriteUpdateResponse,
)
def set_favorite(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    payload: FavoriteUpdateRequest,
    user_id: str = Depends(require_session),
) -> FavoriteUpdateResponse:
    """Toggle the favourite flag on a single email (Provider-First)."""
    return emails_service.set_favorite(
        mailbox_id, account_id, provider_message_id, payload.favorite, user_id,
    )


@favorites_router.post("/favorites/sync", response_model=FavoriteSyncResponse)
def sync_favorites(
    mailbox_id: str,
    account_id: str | None = Query(default=None),
    user_id: str = Depends(require_session),
) -> FavoriteSyncResponse:
    """Reconcile ``is_favorite`` from the provider for one or every account."""
    return emails_service.sync_favorites(mailbox_id, user_id, account_id)


@favorites_router.get(
    "/accounts/{account_id}/emails/{provider_message_id}/reply-context",
    response_model=ReplyContextOut,
)
def get_reply_context(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    action: Literal["reply", "reply_all", "forward"] = Query(...),
    user_id: str = Depends(require_session),
) -> ReplyContextOut:
    """Return the prefill data the composer needs to open a Reply / Forward.

    Read-only — no DB writes, no provider mutations. Mounted on the
    ``favorites_router`` because its URL shape
    (``/mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/...``)
    matches the favourite toggle endpoint; the primary ``emails_router``
    uses a different path layout.
    """
    return emails_service.get_reply_context(
        mailbox_id, account_id, provider_message_id, action, user_id,
    )


@favorites_router.get(
    "/accounts/{account_id}/emails/{provider_message_id}/conversation",
    response_model=ConversationOut,
)
def get_conversation(
    mailbox_id: str,
    account_id: str,
    provider_message_id: str,
    user_id: str = Depends(require_session),
) -> ConversationOut:
    """Return the full message chain of a conversation (conversation viewer).

    Read + lazy sync (not Provider-First — only reads from the provider and
    completes the local copy). Identified by ``provider_message_id`` (not
    ``thread_id``) because Outlook's ``conversationId`` is base64 with
    ``/`` / ``+`` / ``=`` and would break a path segment; the backend
    derives the thread from the message row. Mounted on the
    ``favorites_router`` for the same URL-shape reason as
    ``reply-context``.
    """
    return emails_service.get_conversation(
        mailbox_id, account_id, provider_message_id, user_id,
    )
