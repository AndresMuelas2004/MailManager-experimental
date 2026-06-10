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
    def get_by_id_for_user(
        self, account_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Single-JOIN lookup that proves the account belongs to ``user_id``.

        Returns the account row only when ``account_id`` exists AND the
        owning mailbox's ``owner_user_id`` equals ``user_id``. ``None``
        otherwise — used by cross-account flows (Forward copy from a
        different account) where the service has the account id but
        not the mailbox id. ``None`` collapses to ``AccountNotFound``
        (HTTP 404) uniformly so a foreign account is indistinguishable
        from a missing one (anti-leak D-22).
        """
        raise NotImplementedError

    @abstractmethod
    def list_account_ids_by_user(self, user_id: str) -> list[str]:
        """Single-JOIN listing of every account_id owned by ``user_id``.

        Used by flows that need to validate a flat set of account ids
        against the user's owned catalogue (virtual mailboxes are the
        canonical caller). Replaces the prior O(N_mailboxes) pattern of
        ``mailbox_store.list_by_owner`` followed by one
        ``account_store.list_by_mailbox`` per mailbox.
        """
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
    def get_sync_cursors_for_mailbox(self, mailbox_id: str) -> dict[str, str | None]:
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
    def list_provider_message_ids_not_in(
        self, account_id: str, exclude_ids: list[str],
    ) -> list[str]:
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
        box: str | None,
        tokens: list[str],
        limit: int,
        offset: int,
        *,
        extra_filters: dict[str, Any] | None = None,
        box_in: list[str] | None = None,
        box_not_in: list[str] | None = None,
        distinct_provider_message_id: bool = False,
        group_by_thread: bool = False,
        operator_clauses: list[tuple[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """List email metadata for the given accounts, optionally filtered.

        ``box`` may be ``None`` when ``box_in`` or ``box_not_in`` is
        used instead — virtual mailboxes can match across multiple
        boxes, but the regular inbox listing always passes a single
        ``box`` value.

        ``tokens``: empty means no search filter. Non-empty tokens are
        AND-combined; for each token, the predicate is OR'd across
        ``subject``, ``from_email``, ``from_name`` with accent-/case-
        insensitive substring match (literal — no fuzzy/stemming).
        LIKE metacharacters in user input must be escaped before
        reaching the implementation.

        ``extra_filters``: optional dict with keys taken from a fixed
        whitelist (see the repository). Today supported: ``is_read``
        (bool), ``is_favorite`` (bool), ``from_email`` (str, exact
        match, case-insensitive), ``subject_contains`` (str, substring).
        Unknown keys are ignored — they cannot inject SQL.

        ``distinct_provider_message_id``: when ``True``, collapse rows
        sharing a ``provider_message_id`` (the same provider message
        surfaced under two ``account_id`` rows, i.e. one provider account
        connected under two mailboxes) into a single row BEFORE applying
        ``LIMIT``/``OFFSET``. The surviving row prefers a populated
        ``to_email``, then a populated ``to_name``, then the most recent
        ``received_at``. Only virtual mailboxes need this — the regular
        box listing is always scoped to a single mailbox where the
        duplication is impossible, so it leaves the flag ``False``.

        ``group_by_thread``: when ``True``, collapse each conversation
        (keyed by ``COALESCE(NULLIF(thread_id, ''), provider_message_id)``)
        into its most-recent message — aggregating ``is_read`` (AND),
        ``has_attachments`` / ``is_favorite`` (OR) and projecting
        ``thread_message_count``. Together with
        ``distinct_provider_message_id`` it selects one of four SQL
        templates (the 2x2 matrix). The companion ``count_filtered`` MUST
        receive the SAME ``group_by_thread`` / ``distinct_provider_message_id``
        axes or the paginated ``total`` will not match the listed rows.

        ``operator_clauses``: optional list of ``(kind, typed_value)``
        pairs for the lupa's Gmail-style operators (``from:`` / ``to:`` /
        ``subject:`` / ``has:attachment`` / ``before:`` / ``after:`` /
        ``is:read|unread|favorite``). Each ``kind`` resolves against a
        closed builder registry in the repository and emits an extra
        ANDed clause with per-occurrence parameter names — disjoint from
        ``extra_filters`` and ``tokens`` so repeated operators and
        overlaps with saved filters never collide. ``in:`` is NOT here;
        it is applied by the service as a box override. Unknown kinds are
        ignored. ``None`` / empty means "no operator clauses" and the
        emitted SQL is identical to the pre-operator query.
        """
        raise NotImplementedError

    @abstractmethod
    def count_filtered(
        self,
        account_ids: list[str],
        box: str | None,
        tokens: list[str],
        *,
        extra_filters: dict[str, Any] | None = None,
        box_in: list[str] | None = None,
        box_not_in: list[str] | None = None,
        distinct_provider_message_id: bool = False,
        group_by_thread: bool = False,
        operator_clauses: list[tuple[str, Any]] | None = None,
    ) -> int:
        """Count the email metadata rows that match ``list_filtered``.

        Returns the exact size of the filtered set for the SAME
        ``box`` / ``tokens`` / ``extra_filters`` / ``box_in`` /
        ``box_not_in`` predicates, ignoring ``LIMIT``/``OFFSET``. Backs
        the ``total`` of the paginated listing envelope. The predicates
        are built by the same private helper that feeds ``list_filtered``
        so the count always matches what the listing would return.

        ``distinct_provider_message_id``: when ``True``, count distinct
        ``provider_message_id`` values (matching the deduplicated virtual
        mailbox listing) instead of raw rows.

        ``group_by_thread``: when ``True``, count threads instead of
        messages. It MUST mirror the same axis ``list_filtered`` uses or
        the paginated ``total`` will disagree with the rows returned.

        ``operator_clauses``: same as ``list_filtered`` — the count is
        driven through the same predicate builder so it counts exactly
        the set the listing would return.

        Returns ``0`` without touching the database when ``account_ids``
        is empty (mirrors ``list_filtered`` returning ``[]``).
        """
        raise NotImplementedError

    @abstractmethod
    def update_favorite(
        self,
        account_id: str,
        provider_message_id: str,
        is_favorite: bool,
    ) -> bool:
        """Toggle ``is_favorite`` for a single message.

        Returns ``True`` iff a row was updated. Returns ``False`` when
        no matching row exists (the service translates this into
        ``EmailNotFound``).
        """
        raise NotImplementedError

    @abstractmethod
    def sync_favorites_for_account(
        self,
        account_id: str,
        favorite_ids: list[str],
    ) -> int:
        """Full replacement of the favourite set for one account.

        Marks ``is_favorite = TRUE`` for every ``provider_message_id``
        in ``favorite_ids`` AND ``is_favorite = FALSE`` for every other
        row of the account, in a single atomic statement. Returns the
        number of rows touched (always equals the row count for the
        account; useful for observability).
        """
        raise NotImplementedError

    @abstractmethod
    def set_favorites_true_batch(
        self,
        account_id: str,
        provider_message_ids: list[str],
    ) -> int:
        """Mark a SUBSET of an account's messages favourite in one statement.

        Sets ``is_favorite = TRUE`` for every ``provider_message_id`` in
        ``provider_message_ids`` belonging to ``account_id``. Unlike
        ``sync_favorites_for_account`` it does NOT force the other rows to
        ``FALSE`` — the conversation lazy-sync only knows the thread it just
        fetched, so it must not clear favourites elsewhere in the account.
        One-directional by design. Returns the number of rows updated.
        """
        raise NotImplementedError

    @abstractmethod
    def exists(self, account_id: str, provider_message_id: str) -> bool:
        """Return True iff a row with this (account_id, provider_message_id) pair exists."""
        raise NotImplementedError

    @abstractmethod
    def get_metadata(
        self, account_id: str, provider_message_id: str,
    ) -> dict[str, Any] | None:
        """Return the message's full metadata row (incl. ``thread_id``) as a
        dict, or ``None`` when no matching row exists.

        Projects the same columns + ``mailbox_id`` as the listing query so
        the row maps to ``EmailMetadataOut`` without special-casing. Used
        by the conversation endpoint to resolve the base message's thread
        and to map the singleton viewer response when the message has no
        thread. Malformed UUIDs collapse to ``None`` (treated as "not
        found"), consistent with ``exists``.
        """
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

    @abstractmethod
    def list_recipient_suggestions(
        self,
        account_ids: list[str],
        tokens: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Aggregate distinct recipient-autocomplete suggestions.

        Collects candidate addresses from BOTH the senders of received
        mail (``from_email`` / ``from_name``) and the recipients of sent
        mail (``to_email`` / ``to_name``) across every account in
        ``account_ids``, restricted to boxes other than SPAM / TRASH /
        DELETED, and excluding the user's own account addresses
        (``accounts.email_address``).

        ``tokens`` are AND-combined; for each token the predicate is OR'd
        across the ``(email, name)`` pair with accent-/case-insensitive
        substring match. The tokens MUST already be parsed by the
        service (``parse_search_tokens``) — LIKE metacharacters are
        escaped inside the repository.

        Returns one row per distinct ``lower(email)``, each
        ``{"email": str, "name": str | None, "frequency": int,
        "last_seen": datetime}``, ordered by frequency then recency
        (most-recent non-empty name wins for ``name``), capped at
        ``limit``. Returns ``[]`` without touching the database when
        ``account_ids`` is empty.
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
    def get_by_email(self, email: str) -> dict[str, Any] | None:
        """Lookup a user by email. Returns ``None`` when no row matches.

        Used by the dev-login backdoor to resolve the impersonated
        identity from the ``DEV_LOGIN_EMAIL`` env var. ``email`` is the
        plain ``users.email`` column — there is no separate unique
        constraint, so ``LIMIT 1`` keeps the contract single-row.
        """
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

    @abstractmethod
    def list_existing_source_attachment_ids(
        self, account_id: str, provider_draft_id: str,
    ) -> set[str]:
        """Return the set of ``email_attachments.attachment_id`` values
        already copied into this draft.

        Used by ``copy_attachments_from_email`` for R-12 idempotency:
        a retried copy filters its candidate list against this set so
        already-landed rows are not duplicated. The partial index
        ``idx_draft_attachments_source`` (migration 0030) backs the
        underlying query.
        """
        raise NotImplementedError


class VirtualMailboxStore(ABC):
    """
    Contract for virtual (fake) mailbox persistence.

    Virtual mailboxes are user-defined filtered views over the messages
    that already live in ``email_metadata``. The store owns the CRUD
    over the ``virtual_mailboxes`` table — the actual filtering (scope
    + filter translated to SQL) is the service layer's job.
    """

    @abstractmethod
    def create(self, virtual_mailbox: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get(self, virtual_mailbox_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def list_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update(self, virtual_mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """Full-field replace.

        Returns the updated row when the UPDATE matched, or ``None`` when
        no row matched the id — typically because the row was deleted
        between the service's ownership pre-check and this call (race).
        The service translates ``None`` into a 404 to keep the contract
        symmetric with the rest of the virtual-mailbox surface.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, virtual_mailbox_id: str) -> bool:
        """Delete a virtual mailbox by id. Returns ``True`` iff a row was removed."""
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
