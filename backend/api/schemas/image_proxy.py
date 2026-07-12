"""
Pydantic schemas for the remote-email-image proxy API.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImageProxyPurgeResult(BaseModel):
    """Response payload for ``POST /admin/image-proxy/purge``.

    Structurally identical to ``attachment.PurgeResult`` but kept as its own
    schema for a self-documenting contract (same rationale as the
    ``SpamResponse`` / ``ArchiveResponse`` pair)."""

    purged_count: int = Field(..., ge=0)
    freed_bytes: int = Field(..., ge=0)
