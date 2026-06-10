"""
Account router for provider-agnostic account management.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.routers.routers_helpers import require_session
from api.schemas.account import AccountConnectStartResponse, AccountCreate, AccountOut, AccountUpdate
from api.services import accounts_service


router = APIRouter(prefix="/mailboxes/{mailbox_id}/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(
    mailbox_id: str,
    user_id: str = Depends(require_session),
) -> list[AccountOut]:
    """
    List all accounts for a mailbox.
    """
    return accounts_service.list_accounts(mailbox_id, user_id)


@router.post("", response_model=AccountOut)
def create_account(
    mailbox_id: str,
    payload: AccountCreate,
    user_id: str = Depends(require_session),
) -> AccountOut:
    """
    Create a new account for the mailbox.
    """
    return accounts_service.create_account(mailbox_id, payload, user_id)


@router.get("/{account_id}", response_model=AccountOut)
def get_account(
    mailbox_id: str,
    account_id: str,
    user_id: str = Depends(require_session),
) -> AccountOut:
    """
    Fetch a single account by identifier.
    """
    return accounts_service.get_account(mailbox_id, account_id, user_id)


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    mailbox_id: str,
    account_id: str,
    payload: AccountUpdate,
    user_id: str = Depends(require_session),
) -> AccountOut:
    """
    Update mutable fields of an account.
    """
    return accounts_service.update_account(mailbox_id, account_id, payload, user_id)


@router.delete("/{account_id}")
def delete_account(
    mailbox_id: str,
    account_id: str,
    user_id: str = Depends(require_session),
) -> dict[str, str]:
    """
    Delete an account and invalidate the mailbox manager cache.
    """
    return accounts_service.delete_account(mailbox_id, account_id, user_id)


@router.post("/{account_id}/connect", response_model=AccountConnectStartResponse)
def connect_account(
    mailbox_id: str,
    account_id: str,
    user_id: str = Depends(require_session),
) -> AccountConnectStartResponse:
    """
    Start the interactive connect flow: returns the provider authorization
    URL the user's browser must open. The OAuth callback endpoint completes
    the connection.
    """
    return accounts_service.start_account_connect(mailbox_id, account_id, user_id)
