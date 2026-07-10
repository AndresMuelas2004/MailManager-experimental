"""Fixtures compartidas por los tests espejo del paquete ``gmail_client``."""

from __future__ import annotations

import pytest

from core.email.gmail_client import GmailClient


@pytest.fixture
def client() -> GmailClient:
    return GmailClient(account_label="mb__acct")
