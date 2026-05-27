"""
Pydantic schemas for email API contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.schemas.attachment import AttachmentMetadataOut


class EmailSendRequest(BaseModel):
    """
    Request model for sending an email from a specific account.
    """

    account_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    recipients: list[str] = Field(..., min_length=1)


class AccountSyncDetail(BaseModel):
    """Per-account sync result."""

    account_id: str
    provider: str
    emails_synced: int
    sync_cursor: str | None = None


class SyncResultOut(BaseModel):
    """Response for the sync-metadata endpoint."""

    total_synced: int
    accounts: list[AccountSyncDetail]


class TrashItem(BaseModel):
    provider_message_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)


class TrashActionRequest(BaseModel):
    action: Literal["delete", "restore"]
    items: list[TrashItem] = Field(..., min_length=1)


class TrashActionResult(BaseModel):
    affected: int


class MoveToTrashRequest(BaseModel):
    items: list[TrashItem] = Field(..., min_length=1)


class MoveToTrashResult(BaseModel):
    affected: int


class ReadStatusItem(BaseModel):
    """Single item in a read-status update request."""

    account_id: str = Field(..., min_length=1)
    provider_message_id: str = Field(..., min_length=1)


class ReadStatusRequest(BaseModel):
    """Request to batch-update read/unread status."""

    is_read: bool
    items: list[ReadStatusItem] = Field(..., min_length=1)


class AccountReadStatusDetail(BaseModel):
    """Per-account result of a read-status update."""

    account_id: str
    updated: int


class ReadStatusResponse(BaseModel):
    """Response for the read-status endpoint."""

    updated_count: int
    accounts: list[AccountReadStatusDetail]


class SpamItem(BaseModel):
    """Single item in a spam move/restore request."""

    account_id: str = Field(..., min_length=1)
    provider_message_id: str = Field(..., min_length=1)


class SpamRequest(BaseModel):
    """Request to batch move/restore emails to/from spam."""

    items: list[SpamItem] = Field(..., min_length=1)


class AccountSpamDetail(BaseModel):
    """Per-account result of a spam move/restore operation."""

    account_id: str
    moved: int


class SpamResponse(BaseModel):
    """Response for spam move/restore endpoints."""

    moved_count: int
    accounts: list[AccountSpamDetail]


class EmailContentOut(BaseModel):
    """Full email body content + downloadable attachment metadata.

    The HTML body has already been pipelined through
    ``email_html_pipeline.prepare_email_html`` (CSS sanitisation,
    inline images resolved to ``data:`` URLs per D-13) so the frontend
    can render it directly in a sandboxed iframe.

    ``attachments`` lists every part the user should see as a
    downloadable attachment — inline images that ARE referenced by the
    body stay embedded as ``data:`` URLs and do NOT appear here.
    """

    html_body: str | None = None
    text_body: str | None = None
    attachments: list[AttachmentMetadataOut] = Field(default_factory=list)


class EmailMetadataOut(BaseModel):
    """Single email metadata item returned by the listing endpoint.

    ``has_attachments`` is the denormalised flag persisted on
    ``email_metadata`` (D-09). With the chosen "B.lazy puro" strategy
    it stays ``False`` until the user opens the email for the first
    time and ``get_email_content`` populates ``email_attachments`` —
    after that, the inbox icon (📎) appears for that row.

    ``is_favorite`` is the cross-provider abstraction over Gmail's
    ``STARRED`` label and Outlook's message flag. It is orthogonal to
    ``box`` / ``is_read``: a favourite email can sit in any box, read
    or unread.
    """

    provider_message_id: str
    account_id: str
    mailbox_id: str
    thread_id: str | None = None
    from_email: str
    from_name: str | None = None
    to_email: str | None = None
    to_name: str | None = None
    subject: str | None = None
    received_at: datetime
    is_read: bool
    box: str
    has_attachments: bool = False
    is_favorite: bool = False


class FavoriteUpdateRequest(BaseModel):
    """Request body for the ``PATCH .../{message_id}/favorite`` endpoint."""

    favorite: bool


class FavoriteUpdateResponse(BaseModel):
    """Response after toggling a favourite (mirrors the new state)."""

    provider_message_id: str
    account_id: str
    is_favorite: bool


class FavoriteSyncAccountDetail(BaseModel):
    """Per-account result of the /favorites/sync endpoint."""

    account_id: str
    provider: str
    favorites_synced: int


class FavoriteSyncResponse(BaseModel):
    """Response for the /favorites/sync endpoint."""

    total_synced: int
    accounts: list[FavoriteSyncAccountDetail]


class ReplyContextOut(BaseModel):
    """Response for ``GET .../emails/{pmid}/reply-context``.

    Carries every value the composer needs to open a Reply / Reply All
    / Forward draft: recipients (already computed against the current
    account email — Reply-To and self-reply rules applied), the
    pre-prefixed subject, the plain-text body (header + quoted
    original), and the RFC 5322 threading strings persisted alongside
    the draft.

    ``reply_kind`` mirrors the request's ``action`` query param so the
    frontend can route the response to the right composer mode without
    re-parsing the URL.

    ``original_from_email`` is purely informational — the prefilled
    ``to_recipients`` already contain the correct destination after
    applying R-10 (Reply-To respected). The frontend may use it to
    render contextual hints in the composer header.
    """

    to_recipients: list[str]
    cc_recipients: list[str]
    bcc_recipients: list[str] = Field(default_factory=list)
    subject: str
    body: str
    in_reply_to: str
    references: str
    thread_id: str
    reply_to_message_id: str
    reply_kind: Literal["reply", "reply_all", "forward"]
    original_from_email: str
