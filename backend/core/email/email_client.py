from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EmailMetadata:
    """
    Normalized email metadata returned by provider clients.

    ``to_email`` / ``to_name`` carry the **first** recipient of the
    original ``To`` header — captured during sync so the inbox listing
    can render "Para" without re-hitting the provider. Multi-recipient
    messages still happen but the table has a single column for "Para";
    promoting this to a list is a non-destructive future migration.
    Empty strings (default) when the provider response carries no ``To``
    (rare; service-side notifications mass-mailed via Bcc).
    """
    provider_message_id: str
    thread_id: str
    from_email: str
    from_name: str
    subject: str
    received_at: datetime
    is_read: bool
    box: str  # "ALL_MAIL" | "SENT" | "SPAM" | "TRASH" | "DELETED"
    to_email: str = ""
    to_name: str = ""
    account_id: str = ""  # Stamped by the service layer before persistence


@dataclass
class LabelUpdate:
    """Partial update carrying only label-derived fields for an existing message."""
    provider_message_id: str
    is_read: bool
    box: str  # "ALL_MAIL" | "SENT" | "SPAM" | "TRASH" | "DELETED"


@dataclass
class SyncResult:
    """
    Result of fetch_email_metadata, supporting both bootstrap and incremental sync.

    - upserts: full metadata to insert or update.
    - new_cursor: opaque sync cursor for the next call.
    - deletes: provider_message_ids to remove from persistence.
    - label_updates: partial updates (is_read, box) for messages already persisted.
    """
    upserts: list[EmailMetadata]
    new_cursor: str
    deletes: list[str] = field(default_factory=list)
    label_updates: list[LabelUpdate] = field(default_factory=list)
    is_full_sync: bool = False


@dataclass
class SpamMoveResult:
    """Result of a spam move/restore for a single message."""
    old_id: str
    new_id: str  # Same as old_id for Gmail; different for Outlook


@dataclass
class EmailContent:
    """Full body content of a single email message."""
    html_body: str | None
    text_body: str | None


@dataclass
class ReplyContext:
    """Per-message data the API needs to open a Reply / Reply All / Forward.

    Returned by :py:meth:`EmailClient.fetch_reply_context` and consumed
    by the service layer to compute the pre-filled ``to`` / ``cc`` /
    ``subject`` / ``quoted_body`` for the composer, plus the threading
    metadata persisted in ``drafts`` (R-04 / R-09 / R-10).

    Fields:
    - ``provider_message_id`` — echo of the request id, useful for
      assertions in tests and downstream caches.
    - ``thread_id`` — Gmail ``threadId`` / Outlook ``conversationId``.
    - ``from_email`` / ``from_name`` — parsed ``From`` header.
    - ``reply_to`` — RFC 2822 ``Reply-To`` addresses; empty list when
      the original did not set the header. Honoured per R-10.
    - ``to_recipients`` / ``cc_recipients`` — addresses the original
      went out to; used for Reply All CC computation and for the
      Forward header.
    - ``subject`` — the original ``Subject`` (without re-prefixing).
    - ``body_html`` / ``body_text`` — original body parts; either may be
      ``None``. The service degrades HTML → text via
      :py:func:`core.email.helpers._html_to_text`.
    - ``received_at`` — original ``Date`` header (or provider-side
      timestamp on fallback).
    - ``message_id`` — RFC 5322 ``Message-ID`` (without angle brackets
      already stripped by the client). Used to build ``In-Reply-To``.
    - ``references`` — raw ``References`` header value (or ``""``).
    - ``box`` — provider-side folder (``ALL_MAIL`` / ``SENT`` /
      ``SPAM`` / ``TRASH``). Drives the self-reply override (when
      replying to your own SENT message, the To becomes the original
      To).
    """
    provider_message_id: str
    thread_id: str
    from_email: str
    from_name: str
    reply_to: list[str]
    to_recipients: list[str]
    cc_recipients: list[str]
    subject: str
    body_html: str | None
    body_text: str | None
    received_at: datetime
    message_id: str
    references: str
    box: str = "ALL_MAIL"


@dataclass
class DraftMetadata:
    """
    Normalized draft metadata returned by provider clients after creating a draft.

    The ``body`` field carries plain-text content (D-31). It used to be
    called ``body_html`` but the composer is a plain ``<textarea>`` and
    both providers receive ``text/plain`` MIME, so the name now matches
    the actual semantics. A future rich-text editor will introduce
    ``body_format`` rather than reviving the HTML naming.
    """
    provider_draft_id: str
    to_recipients: list[str]
    cc_recipients: list[str]
    bcc_recipients: list[str]
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass
class AttachmentMetadata:
    """Normalised metadata for a single email attachment.

    Returned by :py:meth:`EmailClient.list_message_attachments`. Only
    contains metadata; the binary is fetched on demand via
    :py:meth:`EmailClient.fetch_attachment_binary`.

    ``part_id`` is populated for Gmail (stable per the API docs) and
    is ``None`` for Outlook. ``provider_attachment_id`` is populated
    for Outlook (the immutable ``attachment.id`` returned by Graph)
    and is ``None`` for Gmail (Gmail's ``attachmentId`` is not declared
    stable so we never persist it as a key — see core_guide.md).
    Together they form the provider-specific cache key used by the
    storage layer (D-06b-clave).
    """
    provider_message_id: str
    part_id: str | None
    provider_attachment_id: str | None
    filename: str
    mime_type: str
    size: int
    content_id: str | None
    is_inline: bool
    position: int


@dataclass
class AttachmentBinary:
    """Decoded attachment binary returned by ``fetch_attachment_binary``."""
    mime_type: str
    filename: str
    data: bytes
    size: int


@dataclass
class DraftAttachmentInput:
    """Input for ``send_draft_with_attachments``.

    Carries everything needed to push a draft attachment to the provider
    during the send flow: the binary, metadata and (for Outlook only)
    the ``provider_attachment_id`` recorded by a previous partial-success
    upload so a retry skips already-uploaded parts (D-27).
    """
    draft_attachment_id: str
    filename: str
    mime_type: str
    data: bytes
    size: int
    position: int
    content_id: str | None = None
    is_inline: bool = False
    provider_attachment_id: str | None = None


@dataclass
class AttachmentUploadResult:
    """Per-attachment outcome of pushing draft attachments to the provider.

    Returned alongside :py:class:`EmailMetadata` from
    :py:meth:`EmailClient.send_draft_with_attachments`. Outlook populates
    ``provider_attachment_id`` for each attachment that succeeded; Gmail
    leaves it as ``None`` because the send is atomic and there is no
    intermediate state to persist.
    """
    draft_attachment_id: str
    provider_attachment_id: str | None


class EmailClient(ABC):
    """
    Abstract base class that defines the contract for any email provider
    (Gmail, Outlook, etc). All concrete clients must implement these methods.
    """

    @abstractmethod
    def authenticate(
        self,
        app_credentials: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Perform any authentication or token refresh needed for this client.
        This method should be called before making API calls.
        """

    @abstractmethod
    def authenticate_silent(
        self,
        app_credentials: dict[str, Any] | None = None,
        user_tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Perform authentication without starting interactive flows.
        Returns updated token payload when a refresh occurs.
        """

    @abstractmethod
    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        """
        Fetch email metadata from the provider.

        Returns a SyncResult with upserts, deletes, label_updates and new_cursor.
        - If sync_cursor is None -> bootstrap (Path 1).
        - If sync_cursor is not None -> attempt incremental (Path 2),
          fallback to bootstrap on failure.
        """

    @abstractmethod
    def send_email(
        self,
        subject: str,
        body: str,
        recipients: list[str],
    ) -> EmailMetadata:
        """
        Send a simple email message using this provider.
        :param subject: Email subject line.
        :param body: Plain text body of the email.
        :param recipients: List of recipient email addresses.
        :return: Metadata of the sent email.
        """

    @abstractmethod
    def create_draft(
        self,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        reply_to_message_id: str | None = None,
        reply_kind: str | None = None,
        original_subject: str | None = None,
    ) -> DraftMetadata:
        """
        Create a draft message at the provider. All fields may be empty
        (empty drafts are allowed). Returns normalized draft metadata.

        ``body`` is plain text (D-31): both providers persist a
        ``text/plain`` MIME at the provider so subsequent draft sends
        can compose a clean ``multipart/mixed`` with attachments.

        Reply / Forward kwargs (all optional, all defaulting to ``None``
        for back-compat with "compose from scratch" callers):

        - ``thread_id`` — Gmail ``threadId`` / Outlook ``conversationId``
          of the original message. Gmail uses it as the third leg of the
          triple-requirement guard; Outlook uses it implicitly via the
          ``createReply`` / ``createForward`` endpoint and does not need
          this value on the wire.
        - ``in_reply_to`` / ``references`` — RFC 5322 strings injected
          into the Gmail MIME so any non-Gmail destination client
          (Outlook, Apple Mail) can re-thread. Outlook ignores them
          (the provider sets its own equivalents server-side).
        - ``reply_to_message_id`` — the original message's provider id.
          Outlook routes the call to ``createReply`` /
          ``createReplyAll`` / ``createForward`` only when this value
          is present alongside ``reply_kind``; absent both → fall back
          to the "blank draft" ``POST /me/messages`` path.
        - ``reply_kind`` — one of ``"reply"`` / ``"reply_all"`` /
          ``"forward"`` / ``None``. Drives the Outlook endpoint
          selection; Gmail uses it only to scope error messages.
        - ``original_subject`` — used by the Gmail-side triple-check
          guard (``validate_reply_threading_coherence``) so the local
          validation matches the subject normalisation rule. ``None``
          disables the subject check (Outlook caller is expected to
          pass ``None``).
        """

    @abstractmethod
    def update_draft(
        self,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        """
        Replace an existing draft's content at the provider (full-field
        replace). All fields may be empty (empty drafts are accepted).
        Returns normalized draft metadata — timestamps are best-effort
        (providers may not return them on update).

        ``body`` is plain text (D-31). The Outlook ``contentType`` is
        ``"Text"`` and Gmail's MIME body is a single ``text/plain`` part
        (no ``multipart/alternative`` wrapping a single part).
        """

    @abstractmethod
    def delete_draft(self, provider_draft_id: str) -> None:
        """
        Delete a draft at the provider. Raises EmailNotAuthenticatedError
        when not authenticated, EmailExternalAPIError on provider failure.
        Returns None (success signaled by absence of exception).
        """

    @abstractmethod
    def send_draft(
        self,
        provider_draft_id: str,
    ) -> EmailMetadata:
        """
        Send an existing draft via the provider API.

        The draft is identified by its provider-assigned ID. After sending,
        the provider marks the draft as sent (Gmail deletes it automatically;
        Outlook transitions the message state).

        Returns metadata of the sent message with ``box="SENT"``.
        Implementations must retry transient failures up to 3 total attempts.
        """

    @abstractmethod
    def fetch_drafts(self) -> list[DraftMetadata]:
        """
        Fetch the most recent drafts from the provider as a flat list.

        Implementations MUST respect a hard cap of ``_DRAFTS_MAX_TOTAL``
        drafts per account (currently 100) and return the most recent
        ones. The exact ordering semantics depend on the provider:
        Outlook uses ``$orderby=lastModifiedDateTime desc`` explicitly;
        Gmail relies on the native API order (reverse-chronological by
        convention). Returns an empty list when the mailbox has no drafts.

        Implementations must raise CoreError subclasses on failure — never
        return a partial list silently.
        """

    @abstractmethod
    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        """Return the subset of message_ids that still exist at the provider."""

    @abstractmethod
    def delete_messages(self, message_ids: list[str]) -> list[str]:
        """Permanently delete messages at the provider.
        Returns the list of provider_message_ids that were successfully deleted.
        Implementations that cannot delete at the provider (e.g. scope limitations)
        should return all IDs as succeeded — the service layer handles local cleanup."""

    @abstractmethod
    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        """Restore messages from trash at the provider.
        items maps provider_message_id → destination_box ('ALL_MAIL', 'SENT', 'SPAM')
        or None when the original box is unknown.
        Returns dict mapping original_id → new_id for successfully restored messages.
        For providers where the ID doesn't change on restore, original_id == new_id."""

    @abstractmethod
    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        """Fetch current metadata for specific messages by ID.
        Returns metadata with box determined by the provider's label/folder state
        using the standard priority (SPAM > SENT > ALL_MAIL).
        Messages that cannot be fetched are silently skipped."""

    @abstractmethod
    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        """Move messages to trash at the provider.
        Returns dict mapping original_id → new_id for successfully trashed messages.
        For providers where the ID doesn't change on trash, original_id == new_id."""

    @abstractmethod
    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        """Mark messages as read/unread at the provider. Returns IDs successfully updated."""

    @abstractmethod
    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        """Toggle the provider's favourite mark for a single message.

        Gmail uses the ``STARRED`` label (added or removed via
        ``users.messages.modify``). Outlook uses the message ``flag``
        property (``flagStatus`` set to ``"flagged"`` /
        ``"notFlagged"`` via ``PATCH /me/messages/{id}``).

        Must raise :py:class:`EmailNotAuthenticatedError` when the
        client is not authenticated and a typed ``CoreError`` subclass
        on any provider-side failure. Returns ``None`` — success is
        signalled by absence of an exception.
        """

    @abstractmethod
    def list_favorite_ids(self) -> list[str]:
        """List the provider's currently-favourite message ids for this account.

        Used by the manual ``/favorites/sync`` endpoint to reconcile the
        local flag against the provider's source of truth (covers
        out-of-band changes from Gmail web, Outlook desktop, mobile…).
        """

    @abstractmethod
    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Move messages to spam at the provider. Returns results for successfully moved messages."""

    @abstractmethod
    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Restore messages from spam at the provider. Returns results for successfully restored messages."""

    @abstractmethod
    def fetch_email_content(self, provider_message_id: str) -> EmailContent:
        """Fetch the full body content for a single email message."""

    @abstractmethod
    def get_account_label(self) -> str:
        """
        Return a human-readable label for this account (for example,
        'personal_gmail', 'university_outlook', etc).
        This helps the manager know which account is which.
        """

    @abstractmethod
    def list_message_attachments(
        self,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        """List downloadable attachments and resolved inline images for a message.

        Returns ``(downloadable, cid_map)``:
        - ``downloadable`` is the list of parts the user should see as
          attachments per the strict inline-vs-attachment rule (D-13):
          parts with ``Content-Disposition: attachment``, plus parts
          marked inline whose ``Content-ID`` is NOT referenced by the
          HTML body, plus parts that carry a ``filename`` without a
          disposition.
        - ``cid_map`` maps each ``Content-ID`` whose CID IS referenced
          by the HTML body to a ``data:`` URL (base64-encoded inline
          image). The HTML pipeline then substitutes ``cid:…`` refs in
          the rendered body.

        Implementations must populate ``part_id`` for Gmail and
        ``provider_attachment_id`` for Outlook (with
        ``Prefer: IdType="ImmutableId"`` per request).
        """

    @abstractmethod
    def fetch_attachment_binary(
        self,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        """Download the binary for a previously-listed attachment.

        Implementations must:
        - Retry transient errors up to 3 attempts with 1s/2s/4s backoff,
          honouring ``Retry-After`` when present (D-16).
        - Raise :py:class:`EmailAttachmentNotFound` on 404/410 (the
          service marks ``unavailable_at`` on the metadata row, D-17).
        - Raise :py:class:`EmailAttachmentDownloadFailed` on 403
          (``detail['reason'] = 'forbidden'``) or persistent 5xx
          (``detail['reason'] = 'unavailable'``) — services translate
          these to 502 / 503 respectively.
        """

    @abstractmethod
    def fetch_reply_context(self, provider_message_id: str) -> ReplyContext:
        """Fetch every piece of data the composer needs to open a Reply /
        Reply All / Forward over ``provider_message_id``.

        Returns a fully populated :py:class:`ReplyContext`. Raises a
        :py:class:`CoreError` subclass on provider failure — the
        service layer translates it into ``EmailReplyContextError``
        (HTTP 502) or :py:class:`EmailNotFound` (HTTP 404) depending
        on the underlying status.
        """

    @abstractmethod
    def send_draft_with_attachments(
        self,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        attachments: list[DraftAttachmentInput],
        *,
        in_reply_to: str | None = None,
        references: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[EmailMetadata, list[AttachmentUploadResult]]:
        """Send a draft together with its locally-stored attachments (D-07, D-27).

        Both providers persist drafts at the server with body and
        recipients (set by ``create_draft`` / ``update_draft``); the
        attachments live only locally until this call. The recipients
        / subject / body parameters are passed because Gmail rebuilds
        the MIME atomically (``drafts.send`` with a fresh
        ``message.raw``) — Outlook does not need them on the wire but
        the contract is uniform across providers.

        ``in_reply_to`` / ``references`` / ``thread_id`` (all keyword-
        only, all optional) carry the threading metadata persisted on
        the local ``drafts`` row when the draft was created as a reply
        or forward (R-01..R-12). Gmail injects ``In-Reply-To`` and
        ``References`` as MIME headers via ``extra_headers`` so the
        destination client (Outlook, Apple Mail, …) re-threads even
        without our ``threadId``. Gmail callers also pass ``thread_id``
        through to the underlying ``drafts.send`` payload so the
        outgoing message attaches to the right Gmail thread. Outlook
        ignores all three — the draft was already created via
        ``createReply`` / ``createForward`` which fixes the
        ``conversationId`` server-side.

        Behaviour:

        - **Gmail** — atomic. Builds ``multipart/mixed`` with
          ``text/plain`` body + every attachment, picks
          :py:class:`GmailSendStrategy` from the total MIME size,
          calls ``drafts.send`` (or the resumable upload variant) once.
          ``AttachmentUploadResult.provider_attachment_id`` is always
          ``None`` — there is no intermediate state to persist. On
          failure raises :py:class:`EmailAttachmentSendFailed` with
          ``detail['failed_attachments']`` populated; nothing local
          is mutated.

        - **Outlook** — non-atomic. For each attachment with no
          ``provider_attachment_id`` (not yet uploaded), picks
          :py:class:`OutlookAttachmentStrategy` and uploads it via
          ``POST /attachments`` (<3 MB) or ``createUploadSession`` +
          chunked ``PUT`` (>=3 MB). Each successful upload yields an
          :py:class:`AttachmentUploadResult` so the caller can persist
          ``provider_attachment_id`` for partial-success resume (D-27).
          After every attachment is in place, ``POST /messages/{id}/send``
          finalises the send. Failure mid-flight raises
          :py:class:`EmailAttachmentSendFailed` and the partial results
          remain valid for a retry.

        Implementations must apply ``Prefer: IdType="ImmutableId"`` to
        every Outlook request that touches messages or attachments.
        """
