"""
Pydantic schemas for the user-folder endpoints (carpetas-y-reglas).

A folder is a user-owned organisation label that materialises in the provider
as a Gmail user-label / Outlook category. ``color`` is a MISSELA-only UI hint
(never synced to the provider in the MVP). Names are case-insensitively unique
per user — a collision surfaces as 409 ``folder_name_conflict``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    color: str | None = Field(default=None, max_length=32)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        # Strip BEFORE ``min_length`` runs so a whitespace-only name collapses to
        # ``""`` and surfaces as 422 here (mirrors VirtualMailboxCreate).
        if isinstance(value, str):
            return value.strip()
        return value


class FolderUpdate(BaseModel):
    """Rename / recolour. Both optional (partial PATCH); at least one is
    expected but an all-empty body is a no-op the service tolerates."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = Field(default=None, max_length=32)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class FolderOut(BaseModel):
    folder_id: str
    owner_user_id: str
    name: str
    color: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: Any) -> Any:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value


class FolderRef(BaseModel):
    """Compact folder reference for chips shown next to each email row."""

    folder_id: str
    name: str
    color: str | None = None


class EmailFoldersOut(BaseModel):
    """The folders an email belongs to (returned after assign/unassign)."""

    folders: list[FolderRef]


class FolderAssignRequest(BaseModel):
    """Body of the per-email folder assign endpoint."""

    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(..., min_length=1)
