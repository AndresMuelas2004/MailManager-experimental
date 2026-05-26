"""
Pydantic schemas for virtual (fake) mailbox endpoints.

The scope/filter language is intentionally constrained at validation
time — only the keys whitelisted in :data:`ALLOWED_FILTER_KEYS` may
appear in ``filter_payload``. Unknown keys raise a 422 at the schema
layer; if they reached the service they would be silently discarded by
the repository, which is fine for forward compatibility but worse for
user-facing diagnostics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ScopeKind = Literal["mailbox", "all", "accounts"]
FilterBox = Literal["ALL_MAIL", "SENT", "SPAM", "TRASH"]


class VirtualMailboxScopePayload(BaseModel):
    """Closed-shape payload — exact field set depends on ``scope_kind``.

    Cross-field validation (e.g. ``scope_kind='mailbox'`` requires
    ``mailbox_id``) is enforced one level up by ``VirtualMailboxCreate``
    / ``VirtualMailboxUpdate``.

    ``extra="forbid"`` makes the schema consistent with
    :class:`VirtualMailboxFilterPayload` — without it a typo like
    ``accountIds`` (camelCase) silently slips through, ``scope_payload``
    is persisted with the unknown key dropped, and the user sees a
    "valid" virtual mailbox that returns empty results because the
    real key was never set.
    """

    model_config = ConfigDict(extra="forbid")

    mailbox_id: str | None = None
    account_ids: list[str] | None = None


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

    # box and box_not_in are mutually exclusive — the model_validator
    # below enforces that. The default (when both are omitted) excludes
    # TRASH and SPAM, matching the Favourites view (see
    # Ignore/Favoritos-Funcionalidad.md).
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
    from_domain: str | None = Field(default=None, min_length=1, max_length=253)
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
    display_name: str = Field(..., min_length=1, max_length=120)
    scope_kind: ScopeKind
    scope_payload: VirtualMailboxScopePayload = Field(
        default_factory=VirtualMailboxScopePayload,
    )
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

    @model_validator(mode="after")
    def _validate_scope_payload(self) -> "VirtualMailboxCreate":
        sp = self.scope_payload
        if self.scope_kind == "mailbox":
            if not sp.mailbox_id:
                raise ValueError(
                    "scope_kind='mailbox' requires scope_payload.mailbox_id."
                )
        elif self.scope_kind == "accounts":
            if not sp.account_ids:
                raise ValueError(
                    "scope_kind='accounts' requires a non-empty scope_payload.account_ids."
                )
        # scope_kind == "all" ignores the payload contents.
        return self


class VirtualMailboxUpdate(VirtualMailboxCreate):
    """Full-field replace — same shape as create."""


class VirtualMailboxOut(BaseModel):
    virtual_mailbox_id: str
    owner_user_id: str
    display_name: str
    scope_kind: ScopeKind
    scope_payload: dict[str, Any]
    filter_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_isoformat(cls, value: Any) -> Any:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
