"""Utilidades sobre lotes de metadatos de email sincronizados."""

from __future__ import annotations

from ..email_client import EmailMetadata


def dedupe_metadata_by_message_id(items: list[EmailMetadata]) -> list[EmailMetadata]:
    """Collapse duplicate ``provider_message_id`` entries, keeping the last one.

    Provider sync streams can legitimately repeat a message: a Graph delta
    window emits one entry per change, so a message that changed twice since
    the stored cursor appears twice, and page-shifted listings can duplicate
    entries across pages on both providers. ``SyncResult.upserts`` must carry
    at most one entry per ``provider_message_id`` — the persistence layer
    upserts the whole batch in a single statement that rejects a batch
    touching the same key twice. The last occurrence wins: delta entries
    arrive oldest-first, so it carries the newest state.
    """
    unique: dict[str, EmailMetadata] = {item.provider_message_id: item for item in items}
    return list(unique.values())
