"""
Pydantic schemas for draft API contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.attachment import DraftAttachmentMetadataOut


# The six reply / forward fields are accepted on both ``DraftCreate``
# and surfaced on ``DraftOut``. They are populated only when the
# composer opens via Reply / Reply All / Forward; "compose from
# scratch" drafts leave them ``None``. The schemas deliberately do NOT
# set ``ConfigDict(extra="forbid")`` to preserve backwards-compat with
# older clients that send unknown fields silently — backend persists
# only what it recognises.
ReplyKind = Literal["reply", "reply_all", "forward"]


class DraftCreate(BaseModel):
    """
    Request model for creating a draft. All fields are optional —
    empty drafts are allowed (matches Gmail/Outlook native behavior).

    ``body`` is sanitised HTML from the rich-text composer (negrita,
    cursiva, subrayado, listas, enlaces). Gmail ships it as a
    ``multipart/alternative`` (derived ``text/plain`` + ``text/html``);
    Outlook ships ``body.contentType = "HTML"``. The ``max_length`` of
    1,000,000 chars caps the body (a body over the cap collapses to a
    Pydantic 422 — the "message too large" guard).

    Reply / forward fields (all optional, ``None`` by default):

    - ``reply_kind`` — composer mode (``reply`` / ``reply_all`` /
      ``forward``). When set together with ``reply_to_message_id`` the
      Outlook client routes via ``createReply`` / ``createReplyAll`` /
      ``createForward``.
    - ``reply_to_message_id`` — provider id of the original message.
    - ``reply_to_account_id`` — UUID of the account owning the original
      message; defensively typed (a free string would let typo'd
      account ids slip through Pydantic).
    - ``thread_id`` — Gmail ``threadId`` / Outlook ``conversationId``.
    - ``in_reply_to`` — RFC 5322 ``In-Reply-To`` header value.
    - ``references_header`` — RFC 5322 ``References`` header chain.
    """

    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    bcc_recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = Field(default="", max_length=1_000_000)
    reply_kind: ReplyKind | None = None
    reply_to_message_id: str | None = None
    reply_to_account_id: UUID | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references_header: str | None = None


class DraftUpdate(BaseModel):
    """
    Request model for updating an existing draft. Semantically a full
    replacement: the provider call overwrites the draft with exactly
    the fields in this payload. All fields are optional with defaults,
    matching DraftCreate — empty fields are valid.

    ``body`` is sanitised HTML (see :py:class:`DraftCreate`), capped at
    1,000,000 chars (over the cap → 422).
    """

    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    bcc_recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = Field(default="", max_length=1_000_000)


class DraftOut(BaseModel):
    """
    Response model for a persisted draft.

    ``attachments`` carries the local-only attachment list (D-07 lazy
    push). The composer hydrates its chip list from this field when
    reopening an existing draft.

    The reply / forward fields mirror :py:class:`DraftCreate`. They
    are present in the response even when ``None`` so the composer
    state hook (``useComposerForm.seedFromDraft``) can propagate them
    into local state when reopening a saved reply draft.
    """

    provider_draft_id: str
    account_id: str
    to_recipients: list[str]
    cc_recipients: list[str]
    bcc_recipients: list[str]
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime
    attachments: list[DraftAttachmentMetadataOut] = Field(default_factory=list)
    reply_kind: ReplyKind | None = None
    reply_to_message_id: str | None = None
    reply_to_account_id: UUID | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references_header: str | None = None


class DraftsAccountSyncDetail(BaseModel):
    """Per-account detail inside a drafts sync response."""

    account_id: str
    provider: str
    drafts_synced: int


class DraftsSyncResultOut(BaseModel):
    """Response model for POST /mailboxes/{mailbox_id}/drafts/sync."""

    total_synced: int
    accounts: list[DraftsAccountSyncDetail]


class DraftSendOut(BaseModel):
    """Response model for sending a draft."""

    provider_message_id: str
    provider: str
    status: str = "sent"
