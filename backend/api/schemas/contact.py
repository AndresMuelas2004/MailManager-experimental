"""Schemas for the recipient-autocomplete (contacts) surface."""

from __future__ import annotations

from pydantic import BaseModel


class ContactSuggestionOut(BaseModel):
    """A single recipient suggestion: an address known from synced mail,
    with the most-recent non-empty display name when available."""

    email: str
    name: str | None = None
