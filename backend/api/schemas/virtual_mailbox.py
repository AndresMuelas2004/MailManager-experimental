"""
Pydantic schemas for virtual (fake) mailbox endpoints.

The filter language is intentionally constrained at validation time —
only the keys whitelisted in :data:`ALLOWED_FILTER_KEYS` may appear in
``filter_payload``. Unknown keys raise a 422 at the schema layer; if
they reached the service they would be silently discarded by the
repository, which is fine for forward compatibility but worse for
user-facing diagnostics.

A virtual mailbox is a flat list of ``account_ids`` plus a filter.
There is no "scope kind" — what used to be ``scope_kind='all'`` /
``scope_kind='mailbox'`` is now snapshot upstream into the explicit
list at creation time (see migration 0032).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FilterBox = Literal["ALL_MAIL", "SENT", "SPAM", "TRASH"]


class VirtualMailboxFilterPayload(BaseModel):
    """Allowed criteria a virtual mailbox can filter by.

    Keep in sync with the repository's ``_EXTRA_FILTER_BUILDERS`` and
    the ``box`` / search slots used by ``email_metadata.LIST_FILTERED``.
    """

    # ``extra="forbid"`` enforces the closed-whitelist contract documented
    # at the module docstring and in repository_guide.md: unknown keys in
    # ``filter_payload`` must surface as 422 at the schema layer. Without
    # this the repository silently drops them (see
    # ``_EXTRA_FILTER_BUILDERS.get(...)`` returning ``None``), which is
    # safe for the SQL side but hides typos from API callers.
    model_config = ConfigDict(extra="forbid")

    # ``min_length=1`` on every free-text criterion turns "send empty string
    # to clear the filter" into a 422 at the schema layer. Without it the
    # repository builders translate ``""`` into wildcard SQL — most visibly,
    # ``subject_contains=""`` becomes ``ILIKE '%%'`` which matches every
    # row, silently turning a "filter by nothing" payload into a full-inbox
    # dump indistinguishable from "no filter at all". Empty string is never
    # a useful filter value; the correct way to clear a filter is to omit
    # the key entirely.
    box: FilterBox | None = None
    box_not_in: list[FilterBox] | None = None
    from_email: str | None = Field(default=None, min_length=1, max_length=320)
    subject_contains: str | None = Field(default=None, min_length=1, max_length=200)
    is_read: bool | None = None
    is_favorite: bool | None = None

    @model_validator(mode="after")
    def _validate_box_exclusivity(self) -> "VirtualMailboxFilterPayload":
        if self.box is not None and self.box_not_in:
            raise ValueError(
                "filter_payload cannot specify both 'box' and 'box_not_in'."
            )
        return self


ALLOWED_FILTER_KEYS = set(VirtualMailboxFilterPayload.model_fields.keys())


class VirtualMailboxCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=120)
    account_ids: list[str] = Field(..., min_length=1)
    filter_payload: VirtualMailboxFilterPayload = Field(
        default_factory=VirtualMailboxFilterPayload,
    )

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_display_name(cls, value: Any) -> Any:
        # Strip BEFORE ``min_length`` runs so a whitespace-only name like
        # ``"   "`` collapses to ``""`` and surfaces as 422 here, rather
        # than slipping through (length 3 passes ``min_length=1``) and
        # being stripped to ``""`` by the service, which would persist
        # an empty display_name in the database and violate the
        # ``min_length=1`` contract.
        if isinstance(value, str):
            return value.strip()
        return value


class VirtualMailboxUpdate(VirtualMailboxCreate):
    """Full-field replace — same shape as create."""


class VirtualMailboxOut(BaseModel):
    virtual_mailbox_id: str
    owner_user_id: str
    display_name: str
    account_ids: list[str]
    filter_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: Any) -> Any:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
