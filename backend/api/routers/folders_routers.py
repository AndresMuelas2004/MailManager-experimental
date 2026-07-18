"""
Folder routers (carpetas-y-reglas).

User-level like ``/virtual-mailboxes`` / ``/contacts`` — no ``/mailboxes/{id}``
prefix, aggregates across every account. The per-email assign / unassign routes
live on ``favorites_router`` (they need the message's account context in the
path); this file only holds the CRUD + the folder email listing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.routers.routers_helpers import require_session
from api.schemas.email import EmailPageOut
from api.schemas.folder import FolderCreate, FolderOut, FolderUpdate
from api.services import folders_service


router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("", response_model=list[FolderOut])
def list_folders(user_id: str = Depends(require_session)) -> list[FolderOut]:
    return folders_service.list_folders(user_id)


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    payload: FolderCreate,
    user_id: str = Depends(require_session),
) -> FolderOut:
    return folders_service.create_folder(user_id, payload)


@router.get("/{folder_id}", response_model=FolderOut)
def get_folder(
    folder_id: str,
    user_id: str = Depends(require_session),
) -> FolderOut:
    return folders_service.get_folder(folder_id, user_id)


@router.patch("/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    user_id: str = Depends(require_session),
) -> FolderOut:
    return folders_service.update_folder(folder_id, user_id, payload)


@router.delete("/{folder_id}")
def delete_folder(
    folder_id: str,
    user_id: str = Depends(require_session),
) -> dict[str, str]:
    folders_service.delete_folder(folder_id, user_id)
    return {"status": "deleted"}


@router.get("/{folder_id}/emails", response_model=EmailPageOut)
def list_folder_emails(
    folder_id: str,
    q: str | None = Query(
        default=None,
        min_length=2,
        max_length=200,
        description=(
            "Search query — same free text and Gmail-style operators as the "
            "regular listing (from:, to:, subject:, has:attachment, "
            "before:/after:, is:read|unread|favorite, in:...), combined with AND."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(require_session),
) -> EmailPageOut:
    return folders_service.list_folder_emails(folder_id, user_id, q, limit, offset)
