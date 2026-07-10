"""Mapeo de una fila de ``email_metadata`` al modelo de respuesta ``EmailMetadataOut``."""

from __future__ import annotations

from typing import Any

from api.schemas.email import EmailMetadataOut


def row_to_email_metadata_out(row: dict[str, Any]) -> EmailMetadataOut:
    """Map an ``email_metadata`` row dict into the API response model.

    Centralised so the regular box listing AND the virtual-mailbox
    listing always project the same fields (including ``is_favorite``
    and ``has_attachments``).
    """
    return EmailMetadataOut(
        provider_message_id=row["provider_message_id"],
        account_id=str(row["account_id"]),
        mailbox_id=str(row["mailbox_id"]),
        thread_id=row.get("thread_id"),
        from_email=row["from_email"],
        from_name=row.get("from_name"),
        to_email=row.get("to_email") or None,
        to_name=row.get("to_name") or None,
        subject=row.get("subject"),
        received_at=row["received_at"],
        is_read=row["is_read"],
        box=row["box"],
        has_attachments=bool(row.get("has_attachments", False)),
        is_favorite=bool(row.get("is_favorite", False)),
        thread_message_count=int(row.get("thread_message_count", 1) or 1),
    )
