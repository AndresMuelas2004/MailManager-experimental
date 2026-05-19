"""
Persistence contracts for the API layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MailboxStore(ABC):
    """
    Contract for mailbox persistence.
    """

    @abstractmethod
    def create(self, mailbox: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get(self, mailbox_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, mailbox_id: str) -> None:
        raise NotImplementedError


class AccountStore(ABC):
    """
    Contract for account persistence.
    """

    @abstractmethod
    def list_by_mailbox(self, mailbox_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get(self, mailbox_id: str, account_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def upsert(self, account: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, mailbox_id: str, account_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_tokens(self, mailbox_id: str, account_id: str, provider: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def upsert_tokens(self, mailbox_id: str, account_id: str, provider: str, token_data: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_sync_cursor(self, mailbox_id: str, account_id: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def update_sync_cursor(self, mailbox_id: str, account_id: str, cursor: str) -> None:
        raise NotImplementedError


class EmailMetadataStore(ABC):
    """
    Contract for email metadata persistence.
    """

    @abstractmethod
    def upsert_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def delete_batch_by_message_ids(self, account_id: str, message_ids: list[str]) -> int:
        raise NotImplementedError

    @abstractmethod
    def update_labels_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def update_read_status_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def list_provider_message_ids(self, account_id: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_trash_emails_by_ids(self, account_id: str, message_ids: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def mark_as_deleted_batch(self, account_id: str, message_ids: list[str]) -> int:
        raise NotImplementedError

    @abstractmethod
    def restore_from_trash_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def restore_from_trash_discovered_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def move_to_trash_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def update_spam_status_batch(self, account_id: str, rows: list[tuple]) -> int:
        raise NotImplementedError

    @abstractmethod
    def list_filtered(
        self,
        account_ids: list[str],
        box: str,
        tokens: list[str],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """List email metadata for the given accounts and box, optionally filtered by search tokens.

        Empty `tokens` means no search filter. Non-empty tokens are AND-combined; for each
        token, the predicate is OR'd across `subject`, `from_email`, `from_name` with
        accent-insensitive case-insensitive substring match (literal — no fuzzy/stemming).
        LIKE metacharacters in user input must be escaped before reaching the implementation.
        """
        raise NotImplementedError

    @abstractmethod
    def exists(self, account_id: str, provider_message_id: str) -> bool:
        """Return True iff a row with this (account_id, provider_message_id) pair exists."""
        raise NotImplementedError

    @abstractmethod
    def update_has_attachments(self, account_id: str, provider_message_id: str) -> None:
        """Recompute and persist ``has_attachments`` from ``email_attachments``.

        Idempotent: derives the flag from the count of non-inline rows
        in ``email_attachments`` for the given message. The single
        helper :py:func:`recompute_has_attachments` (services_helpers)
        is the only place that calls this — see D-09 for why the flag
        is maintained through a centralised path.
        """
        raise NotImplementedError


class EmailContentStore(ABC):
    """
    Contract for email content persistence (full email body).
    """

    @abstractmethod
    def get(self, account_id: str, provider_message_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def upsert(self, account_id: str, provider_message_id: str, html_body: str | None, text_body: str | None) -> None:
        raise NotImplementedError


class DraftStore(ABC):
    """
    Contract for draft persistence.
    """

    @abstractmethod
    def create(self, draft: dict[str, Any]) -> dict[str, Any]:
        """
        Insert a new draft row. Returns the persisted row as a dict,
        including timestamps generated by the database.
        """
        raise NotImplementedError

    @abstractmethod
    def get(
        self,
        provider_draft_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        """
        Return the draft row keyed by (provider_draft_id, account_id),
        or None when no matching row exists. Used as a pre-check before
        calling the provider on draft-update or draft-delete operations.
        """
        raise NotImplementedError

    @abstractmethod
    def update(self, draft: dict[str, Any]) -> dict[str, Any]:
        """
        Update an existing draft row (full-field replace). Returns the
        persisted row including the refreshed ``updated_at`` timestamp.
        The caller must pre-verify the row exists — this method raises
        ``QueryError`` if the update affects zero rows.
        """
        raise NotImplementedError

    @abstractmethod
    def list_by_account(self, account_id: str) -> list[dict[str, Any]]:
        """
        Return all draft rows for a single account, ordered by created_at DESC.
        """
        raise NotImplementedError

    @abstractmethod
    def list_by_mailbox(self, mailbox_id: str) -> list[dict[str, Any]]:
        """
        Return all draft rows across every account that belongs to the
        mailbox, ordered by created_at DESC.
        """
        raise NotImplementedError

    @abstractmethod
    def replace_all_for_account(
        self,
        account_id: str,
        drafts: list[dict[str, Any]],
    ) -> int:
        """
        Replace the full set of drafts for an account atomically. Upserts
        the provided list (keyed by provider_draft_id) and deletes any
        existing rows not present in it. Returns the count of drafts that
        exist for this account after the operation — equals ``len(drafts)``
        on success.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, provider_draft_id: str, account_id: str) -> None:
        """
        Delete a single draft row by composite PK.
        Raises QueryError if the row does not exist.
        """
        raise NotImplementedError


class UserStore(ABC):
    """
    Contract for user persistence.
    """

    @abstractmethod
    def upsert(self, user: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, user_id: str) -> bool:
        raise NotImplementedError


class EmailAttachmentStore(ABC):
    """
    Contract for received-email attachment persistence (D-06, D-09).

    Two physical tables back this contract: ``email_attachments`` for
    metadata and ``email_attachment_blobs`` for the binary. The split
    keeps ``SELECT *`` on the metadata table cheap (no megabyte rows
    accidentally pulled into memory). ``is_downloaded`` is derived in
    SQL via ``EXISTS`` against the blob table, not stored as a column.
    """

    @abstractmethod
    def upsert_batch(self, rows: list[dict[str, Any]]) -> list[str]:
        """Upsert a batch of attachment rows, dispatched per provider key.

        Each row's ``part_id`` (Gmail) or ``provider_attachment_id``
        (Outlook) determines which partial unique index resolves the
        ``ON CONFLICT`` clause (D-06b-clave). Returns the list of
        ``attachment_id`` values that were inserted or updated.
        """
        raise NotImplementedError

    @abstractmethod
    def list_by_message(
        self, account_id: str, provider_message_id: str,
    ) -> list[dict[str, Any]]:
        """List attachment metadata for a single message, ordered by ``position``.

        Each row carries the derived boolean ``is_downloaded`` (true
        iff a row exists in ``email_attachment_blobs``).
        """
        raise NotImplementedError

    @abstractmethod
    def get_for_download(
        self,
        user_id: str,
        mailbox_id: str,
        account_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        """Single-row lookup that proves ownership chain in one query.

        Returns the row only when the attachment belongs to a message
        owned by ``user_id`` via ``mailbox_id`` -> ``account_id``. ``None``
        otherwise. The service translates ``None`` to
        ``AttachmentNotFound`` (no leaking of foreign attachments via
        UUID guessing — D-22).
        """
        raise NotImplementedError

    @abstractmethod
    def get_blob(self, attachment_id: str) -> bytes | None:
        """Return the binary blob for a downloaded attachment, or ``None``."""
        raise NotImplementedError

    @abstractmethod
    def insert_blob(self, attachment_id: str, blob: bytes) -> None:
        """Cache a downloaded binary in ``email_attachment_blobs``.

        Idempotent: a second insert for the same ``attachment_id``
        replaces the existing blob and refreshes ``fetched_at``.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_unavailable(self, attachment_id: str) -> None:
        """Stamp ``unavailable_at = now()`` after a 404/410 from the provider (D-17)."""
        raise NotImplementedError

    @abstractmethod
    def touch_last_accessed(self, attachment_id: str) -> None:
        """Refresh ``last_accessed_at`` after a successful stream (D-15 TTL)."""
        raise NotImplementedError

    @abstractmethod
    def purge_expired_blobs(self) -> tuple[int, int]:
        """Delete blobs whose attachments have not been accessed in 30+ days.

        Returns ``(purged_count, freed_bytes)``. Metadata rows survive
        the purge — re-fetching the attachment from the provider on a
        future open is a cache miss, not a 404 (D-15).
        """
        raise NotImplementedError


class DraftAttachmentStore(ABC):
    """
    Contract for draft-attachment persistence (D-07).

    Drafts attachments live entirely locally until the user sends or
    saves the draft (lazy push). Binary lives inline in the same row;
    bounded by 25 attachments per draft (D-03), the simplification of
    a single table is preferable to a two-table split here.
    """

    @abstractmethod
    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert a draft attachment row and return the persisted view (no blob)."""
        raise NotImplementedError

    @abstractmethod
    def list_by_draft(
        self, account_id: str, provider_draft_id: str,
    ) -> list[dict[str, Any]]:
        """List draft attachment metadata for a draft, ordered by position."""
        raise NotImplementedError

    @abstractmethod
    def list_by_draft_with_blob(
        self, account_id: str, provider_draft_id: str,
    ) -> list[dict[str, Any]]:
        """List draft attachments for a draft with the binary included, ordered
        by position. Used at send time to avoid 1 + N round trips."""
        raise NotImplementedError

    @abstractmethod
    def get(self, draft_attachment_id: str) -> dict[str, Any] | None:
        """Single-row lookup by id without the binary."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, draft_attachment_id: str) -> bool:
        """Delete a single attachment row. Returns True iff a row was removed."""
        raise NotImplementedError

    @abstractmethod
    def update_provider_attachment_id(
        self, draft_attachment_id: str, provider_attachment_id: str,
    ) -> None:
        """Persist the provider's attachment id after a successful upload (D-27)."""
        raise NotImplementedError

    @abstractmethod
    def batch_update_provider_attachment_ids(
        self, pairs: list[tuple[str, str]],
    ) -> int:
        """Persist provider ids for a batch of rows in a single round trip
        (D-27). ``pairs`` is a list of ``(draft_attachment_id,
        provider_attachment_id)`` tuples. Returns the number of rows that
        were actually updated (rows missing because the CASCADE delete
        won the race are silently skipped — the caller is on the
        best-effort post-send hygiene path)."""
        raise NotImplementedError


class SessionStore(ABC):
    """
    Contract for session persistence.
    """

    @abstractmethod
    def create(self, session: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_expired(self) -> None:
        raise NotImplementedError
