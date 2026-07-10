"""Pydantic schemas for the background initial mass backfill status endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class BackfillAccountStatus(BaseModel):
    """Backfill progress for a single account of a mailbox.

    ``done`` is ``True`` for the terminal states (``completed`` / ``failed``).
    Accounts without a backfill job do NOT appear in the list — the frontend
    treats their absence as "no backfill / already loaded".
    """

    account_id: str
    status: Literal["pending", "running", "completed", "failed"]
    fetched_count: int
    target_total: int
    done: bool


class BackfillStatusListOut(BaseModel):
    """Per-mailbox backfill status. ``active`` is ``True`` when any account has
    a ``pending`` / ``running`` job (drives the live "loading…" counter)."""

    accounts: list[BackfillAccountStatus]
    active: bool
