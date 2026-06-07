"""
Virtual (fake) mailbox routers.

Two distinct mounting paths share one file because they share the same
auth dependency and resource family — distinct path segments would
introduce two near-empty router modules.

- ``/virtual-mailboxes`` for CRUD on the definitions.
- ``/virtual-mailboxes/{vmid}/emails`` for the filtered listing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.routers.routers_helpers import require_session
from api.schemas.email import EmailPageOut
from api.schemas.virtual_mailbox import (
    VirtualMailboxCreate,
    VirtualMailboxOut,
    VirtualMailboxUpdate,
)
from api.services import virtual_mailboxes_service


router = APIRouter(prefix="/virtual-mailboxes", tags=["virtual-mailboxes"])


@router.get("", response_model=list[VirtualMailboxOut])
def list_virtual_mailboxes(
    user_id: str = Depends(require_session),
) -> list[VirtualMailboxOut]:
    return virtual_mailboxes_service.list_virtual_mailboxes(user_id)


@router.post("", response_model=VirtualMailboxOut, status_code=201)
def create_virtual_mailbox(
    payload: VirtualMailboxCreate,
    user_id: str = Depends(require_session),
) -> VirtualMailboxOut:
    return virtual_mailboxes_service.create_virtual_mailbox(user_id, payload)


@router.get("/{virtual_mailbox_id}", response_model=VirtualMailboxOut)
def get_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str = Depends(require_session),
) -> VirtualMailboxOut:
    return virtual_mailboxes_service.get_virtual_mailbox(virtual_mailbox_id, user_id)


@router.patch("/{virtual_mailbox_id}", response_model=VirtualMailboxOut)
def update_virtual_mailbox(
    virtual_mailbox_id: str,
    payload: VirtualMailboxUpdate,
    user_id: str = Depends(require_session),
) -> VirtualMailboxOut:
    return virtual_mailboxes_service.update_virtual_mailbox(
        virtual_mailbox_id, user_id, payload,
    )


@router.delete("/{virtual_mailbox_id}")
def delete_virtual_mailbox(
    virtual_mailbox_id: str,
    user_id: str = Depends(require_session),
) -> dict[str, str]:
    virtual_mailboxes_service.delete_virtual_mailbox(virtual_mailbox_id, user_id)
    return {"status": "deleted"}


@router.get("/{virtual_mailbox_id}/emails", response_model=EmailPageOut)
def list_emails_for_virtual_mailbox(
    virtual_mailbox_id: str,
    q: str | None = Query(default=None, min_length=2, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(require_session),
) -> EmailPageOut:
    return virtual_mailboxes_service.list_emails_for_virtual_mailbox(
        virtual_mailbox_id, user_id, q, limit, offset,
    )
