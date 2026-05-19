"""
Pydantic schemas for attachment API contracts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AttachmentMetadataOut(BaseModel):
    """Metadata for a downloadable attachment of a received email.

    Returned inside ``EmailContentOut.attachments``. ``is_downloaded``
    is derived in SQL via ``EXISTS`` against ``email_attachment_blobs``
    so it stays consistent when a TTL purge clears the blob (D-15)
    without dropping the metadata row.
    """

    attachment_id: str
    filename: str
    mime_type: str
    size: int = Field(..., ge=0)
    is_downloaded: bool
    is_unavailable: bool
    position: int = Field(..., ge=0)


class DraftAttachmentMetadataOut(BaseModel):
    """Metadata for a draft attachment surfaced to the composer.

    ``provider_attachment_id`` is exposed for transparency only — the
    frontend does not consume it, but the backend persists it during
    partial-success Outlook uploads (D-27) and including it in the
    response avoids a separate "internal" view that diverges from the
    persisted shape.
    """

    draft_attachment_id: str
    filename: str
    mime_type: str
    size: int = Field(..., ge=0)
    position: int = Field(..., ge=0)
    provider_attachment_id: str | None = None


class DraftAttachmentResponseOut(DraftAttachmentMetadataOut):
    """Response payload for ``POST /drafts/{id}/attachments``.

    Identical to :py:class:`DraftAttachmentMetadataOut`. The class
    aliasing keeps the API contract explicit (the create endpoint
    documents its own response model) without duplicating fields.
    """


class PurgeResult(BaseModel):
    """Response payload for ``POST /admin/attachments/purge`` (D-30)."""

    purged_count: int = Field(..., ge=0)
    freed_bytes: int = Field(..., ge=0)
