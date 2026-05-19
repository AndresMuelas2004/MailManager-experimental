"""
Pydantic schemas for draft API contracts.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from api.schemas.attachment import DraftAttachmentMetadataOut


class DraftCreate(BaseModel):
    """
    Request model for creating a draft. All fields are optional —
    empty drafts are allowed (matches Gmail/Outlook native behavior).

    ``body`` is plain text (D-31): the composer is a plain ``<textarea>``
    and both providers persist the draft as ``text/plain``.
    """

    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    bcc_recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


class DraftUpdate(BaseModel):
    """
    Request model for updating an existing draft. Semantically a full
    replacement: the provider call overwrites the draft with exactly
    the fields in this payload. All fields are optional with defaults,
    matching DraftCreate — empty fields are valid.
    """

    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    bcc_recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


class DraftOut(BaseModel):
    """
    Response model for a persisted draft.

    ``attachments`` carries the local-only attachment list (D-07 lazy
    push). The composer hydrates its chip list from this field when
    reopening an existing draft.
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
