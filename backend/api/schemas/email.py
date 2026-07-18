"""
Pydantic schemas for email API contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.schemas.attachment import AttachmentMetadataOut
from api.schemas.folder import FolderRef


class EmailSendRequest(BaseModel):
    """
    Request model for sending an email from a specific account.

    ``body`` is sanitised HTML from the rich-text composer. It must be
    non-empty (``min_length=1``) and is capped at 1,000,000 chars
    (``max_length`` — over the cap collapses to a Pydantic 422, the
    "message too large" guard).
    """

    account_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1, max_length=1_000_000)
    recipients: list[str] = Field(..., min_length=1)


class AccountSyncDetail(BaseModel):
    """Per-account sync result."""

    account_id: str
    provider: str
    emails_synced: int
    sync_cursor: str | None = None


class AccountSyncFailure(BaseModel):
    """One account that failed inside a partially-successful mailbox sync.

    ``reason`` is a stable category string (NOT the internal exception
    message, so no internal detail leaks to the client — API CLAUDE.md
    §9.4): ``"account_not_connected"`` when the failure is an expired /
    revoked token (auth), ``"sync_failed"`` for any other per-account
    failure. The frontend resolves the account's address from
    ``account_id`` + ``provider`` and uses ``reason`` to phrase the
    non-blocking "reconnect this account" notice.
    """

    account_id: str
    provider: str
    reason: str


class SyncResultOut(BaseModel):
    """Response for the sync-metadata endpoint.

    ``failed_accounts`` carries the per-account failures of a mailbox
    (unified) sync that still succeeded for at least one account: the
    healthy accounts persisted and are reported in ``accounts``, while
    the dead one(s) travel here instead of aborting the call with a 409.
    It is empty on a full success, on a single-account sync, and on the
    total-failure path (which still raises). ``default_factory=list``
    keeps every existing construction site (``total_synced=...,
    accounts=...``) valid.
    """

    total_synced: int
    accounts: list[AccountSyncDetail]
    failed_accounts: list[AccountSyncFailure] = Field(default_factory=list)


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
    # When true, the DB update also flips every OTHER row sharing a thread with
    # the given items (conversation viewer). Off by default so the per-message
    # surfaces (Favoritos, bulk actions) keep marking only the ids they send.
    # Needed because Outlook persists one physical message under several ids
    # (sync vs conversation fetch), and marking a single id leaves the twin
    # unread, keeping the grouped thread row bold.
    propagate_thread: bool = False


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


class ArchiveItem(BaseModel):
    """Single item in an archive/unarchive request."""

    account_id: str = Field(..., min_length=1)
    provider_message_id: str = Field(..., min_length=1)


class ArchiveRequest(BaseModel):
    """Request to batch archive/unarchive emails."""

    items: list[ArchiveItem] = Field(..., min_length=1)


class AccountArchiveDetail(BaseModel):
    """Per-account result of an archive/unarchive operation."""

    account_id: str
    moved: int


class ArchiveResponse(BaseModel):
    """Response for archive/unarchive endpoints."""

    moved_count: int
    accounts: list[AccountArchiveDetail]


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

    ``thread_message_count`` is the number of messages of this row's
    thread present **in this box** (conversation view). It defaults to
    ``1`` and stays ``1`` for non-grouped listings (Favourites) and for
    each message inside a ``ConversationOut``. In grouped mode the row
    represents a whole thread, so ``is_read`` / ``has_attachments`` /
    ``is_favorite`` are the aggregated thread state, not a single
    message's.
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
    thread_message_count: int = 1
    # Folders this email belongs to (chips). Filled by the listing service in a
    # second batch pass AFTER ``row_to_email_metadata_out`` maps the row — the
    # mapper never projects it, so it defaults to ``[]`` when the service does
    # not enrich the page (e.g. conversation viewer messages).
    folders: list[FolderRef] = Field(default_factory=list)


class EmailPageOut(BaseModel):
    """Paginated listing envelope for email metadata.

    Wraps the page of rows with the exact ``total`` of the filtered set
    so the frontend can render "X–Y of Z" and numbered pages. ``total``
    is the count of the WHOLE filtered set (same ``box`` / ``q`` /
    ``favorite`` / accounts), NOT of this page, and reflects only what
    is synced into the local copy — it is never the provider's live
    mailbox size. ``limit`` / ``offset`` echo the values actually
    applied, so the client derives ``page = offset / limit + 1`` and
    ``total_pages = ceil(total / limit)`` without ambiguity. "Has more"
    is derivable (``offset + len(items) < total``) and intentionally not
    a separate field.
    """

    items: list[EmailMetadataOut]
    total: int
    limit: int
    offset: int


class AccountUnreadDetail(BaseModel):
    """Per-account unread count for a single box."""

    account_id: str
    unread: int


class UnreadCountOut(BaseModel):
    """Unread-message counts for a mailbox + box.

    ``total`` is the mailbox-wide sum across all accounts; ``accounts``
    carries the per-account breakdown (every account of the mailbox,
    including those with 0). Counts individual unread messages (not
    threads). Reflects only the locally synced copy, never the provider's
    live mailbox. ``box`` echoes the requested value (ALL_MAIL | SPAM).
    """

    mailbox_id: str
    box: str
    total: int
    accounts: list[AccountUnreadDetail]


class ConversationOut(BaseModel):
    """Full message chain of a conversation (conversation viewer).

    ``messages`` is ordered **chronologically ascending** (oldest
    first), mapped from the provider's fresh thread state on this open —
    so a message that moved box or was read outside the app is reflected
    immediately. Each item is an :py:class:`EmailMetadataOut`; bodies are
    NOT included here — the viewer fetches each body lazily via
    ``GET .../emails/{provider_message_id}/content``. ``has_attachments``
    of each message is always ``False`` here (B.lazy): the per-message
    clip appears once its body is opened and attachments are discovered.

    ``thread_id`` is ``''`` when the base message has no thread (a
    single-message conversation), in which case ``messages`` holds
    exactly that one message and no provider call was made.
    """

    thread_id: str
    messages: list[EmailMetadataOut]


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
    pre-prefixed subject, the **HTML** body (attribution line + the
    original quoted inside a ``<blockquote>``; built by
    ``core.email.helpers.build_quoted_body_html``), and the RFC 5322
    threading strings persisted alongside the draft. The user composes
    above the quote; the composer seeds with this HTML.

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
