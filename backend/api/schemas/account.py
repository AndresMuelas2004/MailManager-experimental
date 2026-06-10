"""
Pydantic schemas for account API contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    """
    Request model for creating an account under a mailbox.
    """

    provider: str = Field(..., min_length=1)
    display_label: str = Field(
        ...,
        min_length=1,
        max_length=120,
    )
    config: dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    """
    Request model for updating mutable account fields.
    """

    display_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    config: dict[str, Any] | None = None


class AccountOut(BaseModel):
    """
    Response model for account data.
    """

    account_id: str
    mailbox_id: str
    provider: str
    display_label: str
    config: dict[str, Any]
    email_address: str | None = None


class AccountConnectStartResponse(BaseModel):
    """
    Response model for starting the interactive account-connect flow.

    The connection is not established yet: the client must open
    ``authorization_url`` in the user's browser; the provider redirects to
    the API's OAuth callback, which completes the exchange and persists the
    tokens.
    """

    provider: str
    account_id: str
    account_label: str
    authorization_url: str
    state: str
