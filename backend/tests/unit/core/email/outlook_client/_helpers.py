"""Builders compartidos por los tests espejo del paquete ``outlook_client``."""

from __future__ import annotations

from core.email.outlook_client import OutlookClient, _DELTA_FOLDERS


def _make_graph_message(
    msg_id: str = "msg1",
    *,
    conversation_id: str = "conv1",
    from_address: str = "alice@example.com",
    from_name: str = "Alice",
    subject: str = "Hello",
    received: str = "2025-06-01T12:00:00Z",
    is_read: bool = True,
    parent_folder_id: str = "",
) -> dict:
    """Build a Graph message resource for testing."""
    msg: dict = {
        "id": msg_id,
        "conversationId": conversation_id,
        "from": {"emailAddress": {"address": from_address, "name": from_name}},
        "subject": subject,
        "receivedDateTime": received,
        "isRead": is_read,
    }
    if parent_folder_id:
        msg["parentFolderId"] = parent_folder_id
    return msg


def _make_authenticated_client() -> OutlookClient:
    client = OutlookClient(account_label="mb__outlook")
    client._access_token = "token"
    return client


def _make_folder_cursor(**overrides: str) -> str:
    """Build a JSON cursor with per-folder deltaLinks."""
    folders = {}
    for folder in _DELTA_FOLDERS:
        folders[folder] = overrides.get(folder, f"https://delta-{folder}")
    return OutlookClient._encode_folder_cursors(folders)
