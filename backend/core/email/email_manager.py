from __future__ import annotations

from typing import Any
from .email_client import (
    AttachmentBinary,
    AttachmentMetadata,
    AttachmentUploadResult,
    ConversationMessage,
    DraftAttachmentInput,
    DraftMetadata,
    EmailClient,
    EmailContent,
    EmailMetadata,
    ReplyContext,
    SpamMoveResult,
    SyncResult,
)
from .errors import (
    CoreError,
    EmailAccountNotFoundError,
    EmailAccountRecordError,
    EmailDuplicateAccountLabelError,
    EmailExternalAPIError,
    EmailProviderConfigError,
)
from .gmail_client import GmailClient
from .outlook_client import OutlookClient


class EmailManager:
    """
    Coordinator for multiple EmailClient instances.
    This class is responsible for orchestrating multi-account flows.
    """

    def __init__(self) -> None:
        """
        Initialize the manager with empty client and error registries.
        """
        self._clients: list[EmailClient] = []
        self._last_errors: dict[str, Exception] = {}

    def add_account_record(self, record: dict[str, Any]) -> None:
        """
        Build and register an EmailClient based on a persisted account record.
        """
        mailbox_id = str(record.get("mailbox_id") or "")
        account_id = str(record.get("account_id") or "")
        provider = str(record.get("provider") or "").lower()
        if not mailbox_id:
            raise EmailAccountRecordError("Account record is missing mailbox_id.")
        if not account_id:
            raise EmailAccountRecordError("Account record is missing account_id.")
        if not provider:
            raise EmailAccountRecordError("Account record is missing provider.")

        account_label = f"{mailbox_id}__{account_id}"
        client = self._build_client(provider, account_label)
        self.add_client(client)

    def _build_client(self, provider: str, account_label: str) -> EmailClient:
        if provider == "gmail":
            return GmailClient(account_label=account_label)
        if provider == "outlook":
            return OutlookClient(account_label=account_label)
        raise EmailProviderConfigError(f"Unknown provider '{provider}'.")

    def add_client(self, client: EmailClient) -> None:
        """
        Register a new EmailClient with a unique account label.
        """
        new_label = client.get_account_label()
        for existing in self._clients:
            if existing.get_account_label() == new_label:
                raise EmailDuplicateAccountLabelError(f"Account label '{new_label}' already exists.")
        self._clients.append(client)

    def _get_client_or_raise(self, account_label: str) -> EmailClient:
        """
        Return the client matching *account_label* or raise EmailAccountNotFoundError.
        """
        for client in self._clients:
            if client.get_account_label() == account_label:
                return client
        raise EmailAccountNotFoundError(
            f"Account '{account_label}' not found.",
            {"account_label": account_label},
        )

    def authenticate_all_silent(
        self,
        auth_payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Authenticate all registered clients without interactive flows.
        """
        self._last_errors = {}
        refreshed_tokens: dict[str, dict[str, Any]] = {}
        for client in self._clients:
            try:
                if auth_payloads is None:
                    updated = client.authenticate_silent()
                else:
                    app_credentials = None
                    user_tokens = None
                    payload = auth_payloads.get(client.get_account_label())
                    if payload is not None:
                        app_credentials, user_tokens = payload
                    updated = client.authenticate_silent(app_credentials, user_tokens)
                if isinstance(updated, dict) and updated:
                    refreshed_tokens[client.get_account_label()] = updated
            except Exception as exc:
                self._last_errors[client.get_account_label()] = exc
        return refreshed_tokens

    def begin_connect(
        self,
        account_label: str,
        app_credentials: dict[str, Any] | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """
        Start the user-driven OAuth flow for a single account by its label.
        Returns {"authorization_url", "state", "flow_state"} from the client.
        """
        self._last_errors = {}
        client = self._get_client_or_raise(account_label)
        try:
            return client.begin_interactive_auth(app_credentials, redirect_uri)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected begin_connect error ({type(exc).__name__}): {exc}"
            ) from exc

    def complete_connect(
        self,
        account_label: str,
        app_credentials: dict[str, Any] | None = None,
        flow_state: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """
        Finish the user-driven OAuth flow for a single account by its label.
        Returns the wrapped account tokens produced by the client.
        """
        self._last_errors = {}
        client = self._get_client_or_raise(account_label)
        try:
            return client.complete_interactive_auth(app_credentials, flow_state, code)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected complete_connect error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_all_email_metadata(
        self,
        sync_cursors: dict[str, str | None] | None = None,
    ) -> dict[str, SyncResult]:
        """
        Fetch email metadata from all clients.
        Returns {account_label: SyncResult}.
        """
        self._last_errors = {}
        results: dict[str, SyncResult] = {}
        for client in self._clients:
            label = client.get_account_label()
            cursor = (sync_cursors or {}).get(label)
            try:
                results[label] = client.fetch_email_metadata(sync_cursor=cursor)
            except Exception as exc:
                self._last_errors[label] = exc
        return results

    def verify_message_existence(
        self,
        account_label: str,
        message_ids: list[str],
    ) -> list[str]:
        client = self._get_client_or_raise(account_label)
        try:
            return client.verify_message_existence(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected verify_message_existence error ({type(exc).__name__}): {exc}"
            ) from exc

    def delete_messages(self, account_label: str, message_ids: list[str]) -> list[str]:
        client = self._get_client_or_raise(account_label)
        try:
            return client.delete_messages(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected delete_messages error ({type(exc).__name__}): {exc}"
            ) from exc

    def restore_from_trash(self, account_label: str, items: dict[str, str | None]) -> dict[str, str]:
        client = self._get_client_or_raise(account_label)
        try:
            return client.restore_from_trash(items)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected restore_from_trash error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_messages_metadata(
        self, account_label: str, message_ids: list[str],
    ) -> list[EmailMetadata]:
        client = self._get_client_or_raise(account_label)
        try:
            return client.fetch_messages_metadata(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected fetch_messages_metadata error ({type(exc).__name__}): {exc}"
            ) from exc

    def move_to_trash(self, account_label: str, message_ids: list[str]) -> dict[str, str]:
        client = self._get_client_or_raise(account_label)
        try:
            return client.move_to_trash(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected move_to_trash error ({type(exc).__name__}): {exc}"
            ) from exc

    def send_email_from_account(
        self,
        account_label: str,
        subject: str,
        body: str,
        recipients: list[str],
    ) -> EmailMetadata:
        """
        Send an email using the client that matches the requested account label.
        Returns metadata of the sent email.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.send_email(subject, body, recipients)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected send_email error ({type(exc).__name__}): {exc}"
            ) from exc

    def send_draft(
        self,
        account_label: str,
        provider_draft_id: str,
    ) -> EmailMetadata:
        """
        Send an existing draft using the client that matches the requested
        account label. Returns metadata of the sent message.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.send_draft(provider_draft_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected send_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def create_draft(
        self,
        account_label: str,
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
        Create a draft using the client that matches the requested account label.
        Body is HTML.

        The reply / forward kwargs (all optional) are propagated to the
        underlying client. Gmail uses ``thread_id`` + ``in_reply_to`` /
        ``references`` to stitch the outgoing message into the thread;
        Outlook uses ``reply_to_message_id`` + ``reply_kind`` to route
        through ``createReply`` / ``createReplyAll`` / ``createForward``.
        See :py:meth:`EmailClient.create_draft` for the full contract.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.create_draft(
                to_recipients, cc_recipients, bcc_recipients, subject, body,
                thread_id=thread_id,
                in_reply_to=in_reply_to,
                references=references,
                reply_to_message_id=reply_to_message_id,
                reply_kind=reply_kind,
                original_subject=original_subject,
            )
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected create_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_reply_context(
        self, account_label: str, provider_message_id: str,
    ) -> ReplyContext:
        """Delegate ``fetch_reply_context`` to the matching client.

        Returns a fully populated :py:class:`ReplyContext`. The service
        layer consumes this to compute the composer prefill (To / Cc /
        Subject / quoted body) and the threading metadata persisted on
        the new draft row.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.fetch_reply_context(provider_message_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected fetch_reply_context error ({type(exc).__name__}): {exc}"
            ) from exc

    def update_draft(
        self,
        account_label: str,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        """
        Update an existing draft using the client that matches the
        requested account label. Full-field replacement semantics — the
        caller passes every field; the provider overwrites the draft
        with exactly those values. Body is HTML.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.update_draft(
                provider_draft_id,
                to_recipients, cc_recipients, bcc_recipients,
                subject, body,
            )
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected update_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def delete_draft(self, account_label: str, provider_draft_id: str) -> None:
        """
        Delete a draft using the client that matches the requested account label.
        """
        client = self._get_client_or_raise(account_label)
        try:
            client.delete_draft(provider_draft_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected delete_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_all_drafts(self) -> dict[str, list[DraftMetadata]]:
        """
        Fetch drafts from every registered client. Returns
        ``{account_label: list[DraftMetadata]}``. Per-account failures
        are captured in ``self._last_errors`` (mirroring the error
        aggregation behavior of ``fetch_all_email_metadata``).
        """
        self._last_errors = {}
        results: dict[str, list[DraftMetadata]] = {}
        for client in self._clients:
            label = client.get_account_label()
            try:
                results[label] = client.fetch_drafts()
            except Exception as exc:
                self._last_errors[label] = exc
        return results

    def update_read_status(
        self,
        account_label: str,
        message_ids: list[str],
        is_read: bool,
    ) -> list[str]:
        """Mark messages as read/unread for the given account. Returns successfully updated IDs."""
        client = self._get_client_or_raise(account_label)
        try:
            return client.update_read_status(message_ids, is_read)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected update_read_status error ({type(exc).__name__}): {exc}"
            ) from exc

    def set_favorite(
        self,
        account_label: str,
        provider_message_id: str,
        is_favorite: bool,
    ) -> None:
        """Toggle the provider's favourite flag for a single message."""
        client = self._get_client_or_raise(account_label)
        try:
            client.set_favorite(provider_message_id, is_favorite)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected set_favorite error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_favorite_ids(self, account_label: str) -> list[str]:
        """Return the provider's current favourite message ids for one account."""
        client = self._get_client_or_raise(account_label)
        try:
            return client.list_favorite_ids()
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected list_favorite_ids error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_all_favorite_ids(self) -> dict[str, list[str]]:
        """Per-account favourite-id listing — used by the multi-account sync.

        Mirrors :py:meth:`fetch_all_drafts` shape: returns
        ``{account_label: list[str]}`` and accumulates per-client errors
        in ``self._last_errors`` so the service layer can decide how to
        surface partial failures.
        """
        self._last_errors = {}
        results: dict[str, list[str]] = {}
        for client in self._clients:
            label = client.get_account_label()
            try:
                results[label] = client.list_favorite_ids()
            except Exception as exc:
                self._last_errors[label] = exc
        return results

    def move_to_spam(
        self,
        account_label: str,
        message_ids: list[str],
    ) -> list[SpamMoveResult]:
        """Move messages to spam for the given account. Returns results for successfully moved messages."""
        client = self._get_client_or_raise(account_label)
        try:
            return client.move_to_spam(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected move_to_spam error ({type(exc).__name__}): {exc}"
            ) from exc

    def restore_from_spam(
        self,
        account_label: str,
        message_ids: list[str],
    ) -> list[SpamMoveResult]:
        """Restore messages from spam for the given account. Returns results for successfully restored messages."""
        client = self._get_client_or_raise(account_label)
        try:
            return client.restore_from_spam(message_ids)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected restore_from_spam error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_content_with_attachments(
        self, account_label: str, provider_message_id: str,
    ) -> tuple[EmailContent, list[AttachmentMetadata], dict[str, str]]:
        """Body + attachments + inline ``cid_map`` in a single provider read.

        Delegates to the matching client's
        :py:meth:`EmailClient.fetch_content_with_attachments`. Used by the
        cache-aside content endpoint AND the sync-time content prefetch —
        both need the body and the attachment list discovered together so a
        pre-cached message still surfaces its attachments on open (a cache
        hit never re-discovers them).
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.fetch_content_with_attachments(provider_message_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected fetch_content_with_attachments error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_conversation(
        self, account_label: str, thread_id: str,
    ) -> list[ConversationMessage]:
        """Fetch every message of a thread (metadata + state, NO body).

        Delegates to the matching client's
        :py:meth:`EmailClient.fetch_conversation`. Used by the
        conversation viewer to reconstruct the full thread (including
        messages the app never synced) and lazily complete the local
        mailbox copy.
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.fetch_conversation(thread_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected fetch_conversation error ({type(exc).__name__}): {exc}"
            ) from exc

    def list_message_attachments(
        self,
        account_label: str,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        """List the downloadable attachments + inline cid_map for a message.

        Delegates to the provider client's
        :py:meth:`EmailClient.list_message_attachments`. Used by the
        service layer in the cache-aside flow to populate
        ``email_attachments`` (D-09) and resolve referenced ``cid:``
        images for the rendered HTML (D-13).
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.list_message_attachments(provider_message_id)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected list_message_attachments error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_attachment_binary(
        self,
        account_label: str,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        """Download a single attachment binary for the given account."""
        client = self._get_client_or_raise(account_label)
        try:
            return client.fetch_attachment_binary(provider_message_id, attachment)
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected fetch_attachment_binary error ({type(exc).__name__}): {exc}"
            ) from exc

    def send_draft_with_attachments(
        self,
        account_label: str,
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

        ``in_reply_to`` / ``references`` / ``thread_id`` carry the
        threading metadata persisted on the local ``drafts`` row for
        reply / forward sends; Gmail injects the headers + ``threadId``
        into the wire send, Outlook ignores them (the provider already
        stitched the thread server-side at draft creation).
        """
        client = self._get_client_or_raise(account_label)
        try:
            return client.send_draft_with_attachments(
                provider_draft_id,
                to_recipients,
                cc_recipients,
                bcc_recipients,
                subject,
                body,
                attachments,
                in_reply_to=in_reply_to,
                references=references,
                thread_id=thread_id,
            )
        except CoreError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Unexpected send_draft_with_attachments error ({type(exc).__name__}): {exc}"
            ) from exc

    def get_last_errors(self) -> dict[str, Exception]:
        """
        Return a snapshot of the most recent errors per account label.
        """
        return dict(self._last_errors)
