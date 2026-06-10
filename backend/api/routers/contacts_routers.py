"""Recipient-autocomplete (contacts) HTTP surface — user-level."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.routers.routers_helpers import require_session
from api.schemas.contact import ContactSuggestionOut
from api.services import contacts_service


router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/suggestions", response_model=list[ContactSuggestionOut])
def suggest_contacts(
    q: str = Query(
        ...,
        min_length=2,
        max_length=200,
        description=(
            "Fragment typed in a recipient field. Matched as accent-/case-"
            "insensitive substring against known addresses and names; "
            "whitespace-only collapses to no suggestions."
        ),
    ),
    limit: int = Query(default=8, ge=1, le=20),
    user_id: str = Depends(require_session),
) -> list[ContactSuggestionOut]:
    return contacts_service.suggest_contacts(user_id, q, limit)
