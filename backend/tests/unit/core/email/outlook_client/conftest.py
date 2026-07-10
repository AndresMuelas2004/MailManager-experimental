"""Fixtures compartidas por los tests espejo del paquete ``outlook_client``."""

from __future__ import annotations

import pytest

from core.email.outlook_client import OutlookClient


@pytest.fixture
def client() -> OutlookClient:
    return OutlookClient(account_label="mb__outlook")


@pytest.fixture
def authenticated_client() -> OutlookClient:
    client = OutlookClient(account_label="mb__acct")
    client._access_token = "test-token"
    return client
