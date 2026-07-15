"""Shared fake email client and metadata builder used by tests."""

from __future__ import annotations

from datetime import datetime

from core.email import (
    AttachmentBinary,
    AttachmentMetadata,
    AttachmentUploadResult,
    BackfillPage,
    ConversationMessage,
    DraftAttachmentInput,
    DraftMetadata,
    EmailClient,
    EmailContent,
    EmailMetadata,
    FavoriteCandidate,
    LabelUpdate,
    SpamMoveResult,
    SyncResult,
)
from core.email.email_client import ReplyContext


DEFAULT_RECEIVED_AT = datetime(2024, 1, 1, 12, 0, 0)
DEFAULT_SYNC_CURSOR = "fake_cursor_12345"


def build_metadata(
    provider_message_id: str = "m1",
    thread_id: str = "t1",
    from_email: str = "sender@example.com",
    from_name: str = "Sender",
    subject: str = "subject",
    received_at: datetime | None = None,
    is_read: bool = False,
    box: str = "ALL_MAIL",
    is_favorite: bool = False,
    account_id: str = "",
) -> EmailMetadata:
    """Build a normalized ``EmailMetadata`` with sensible defaults."""
    if received_at is None:
        received_at = DEFAULT_RECEIVED_AT
    return EmailMetadata(
        provider_message_id=provider_message_id,
        thread_id=thread_id,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        received_at=received_at,
        is_read=is_read,
        box=box,
        is_favorite=is_favorite,
        account_id=account_id,
    )


def build_conversation_message(
    provider_message_id: str = "m1",
    thread_id: str = "t1",
    from_email: str = "sender@example.com",
    from_name: str = "Sender",
    subject: str = "subject",
    received_at: datetime | None = None,
    is_read: bool = True,
    is_favorite: bool = False,
    box: str = "ALL_MAIL",
    to_email: str = "",
    to_name: str = "",
    account_id: str = "",
) -> ConversationMessage:
    """Build a ``ConversationMessage`` with sensible defaults.

    ``account_id`` defaults to ``""`` mirroring the provider contract: the
    service stamps it before persistence (same as ``EmailMetadata``).
    """
    if received_at is None:
        received_at = DEFAULT_RECEIVED_AT
    return ConversationMessage(
        provider_message_id=provider_message_id,
        thread_id=thread_id,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        received_at=received_at,
        is_read=is_read,
        is_favorite=is_favorite,
        box=box,
        to_email=to_email,
        to_name=to_name,
        account_id=account_id,
    )


def build_favorite_candidate(
    provider_message_id: str = "m1",
    received_at: datetime | None = None,
    from_email: str = "sender@example.com",
    subject: str = "subject",
) -> FavoriteCandidate:
    """Build an Outlook-shaped ``FavoriteCandidate`` (identity fields
    populated) with sensible defaults. For a Gmail-shaped candidate (no
    identity — the reconciliation short-circuits), construct
    ``FavoriteCandidate(provider_message_id=...)`` directly: its identity
    fields already default to ``None``.
    """
    if received_at is None:
        received_at = DEFAULT_RECEIVED_AT
    return FavoriteCandidate(
        provider_message_id=provider_message_id,
        received_at=received_at,
        from_email=from_email,
        subject=subject,
    )


class FakeEmailClient(EmailClient):
    """In-memory fake that can simulate provider successes and failures."""

    def __init__(
        self,
        account_label: str,
        *,
        auth_exc: Exception | None = None,
        auth_silent_exc: Exception | None = None,
        fetch_exc: Exception | None = None,
        send_exc: Exception | None = None,
        verify_exc: Exception | None = None,
        delete_exc: Exception | None = None,
        restore_exc: Exception | None = None,
        move_to_trash_exc: Exception | None = None,
        fetch_messages_metadata_exc: Exception | None = None,
        update_read_status_exc: Exception | None = None,
        move_to_spam_exc: Exception | None = None,
        restore_from_spam_exc: Exception | None = None,
        move_to_archive_exc: Exception | None = None,
        restore_from_archive_exc: Exception | None = None,
        fetch_content_exc: Exception | None = None,
        create_draft_exc: Exception | None = None,
        update_draft_exc: Exception | None = None,
        delete_draft_exc: Exception | None = None,
        fetch_drafts_exc: Exception | None = None,
        send_draft_exc: Exception | None = None,
        list_message_attachments_exc: Exception | None = None,
        fetch_attachment_binary_exc: Exception | None = None,
        send_draft_with_attachments_exc: Exception | None = None,
        set_favorite_exc: Exception | None = None,
        list_favorite_ids_exc: Exception | None = None,
        list_favorite_ids_return: list[str] | None = None,
        list_favorite_candidates_return: list[FavoriteCandidate] | None = None,
        fetch_reply_context_exc: Exception | None = None,
        fetch_reply_context_return: ReplyContext | None = None,
        fetch_conversation_exc: Exception | None = None,
        fetch_conversation_return: list[ConversationMessage] | None = None,
        list_message_attachments_return: tuple[list[AttachmentMetadata], dict[str, str]] | None = None,
        fetch_attachment_binary_return: AttachmentBinary | None = None,
        send_draft_with_attachments_return: tuple[EmailMetadata, list[AttachmentUploadResult]] | None = None,
        metadata: list[EmailMetadata] | None = None,
        sync_cursor_return: str = DEFAULT_SYNC_CURSOR,
        auth_return: dict | None = None,
        auth_silent_return: dict | None = None,
        deletes: list[str] | None = None,
        label_updates: list[LabelUpdate] | None = None,
        existing_message_ids: list[str] | None = None,
        is_full_sync: bool = False,
        delete_return: list[str] | None = None,
        restore_return: dict[str, str] | None = None,
        move_to_trash_return: dict[str, str] | None = None,
        move_to_archive_return: list[SpamMoveResult] | None = None,
        fetch_messages_metadata_return: list[EmailMetadata] | None = None,
        email_content: EmailContent | None = None,
        create_draft_return: DraftMetadata | None = None,
        update_draft_return: DraftMetadata | None = None,
        fetch_drafts_return: list[DraftMetadata] | None = None,
        send_draft_return: EmailMetadata | None = None,
        capture_backfill_anchor_return: str = "backfill_anchor",
        capture_backfill_anchor_exc: Exception | None = None,
        fetch_backfill_page_return: BackfillPage | None = None,
        fetch_backfill_pages: list[BackfillPage] | None = None,
        fetch_backfill_page_exc: Exception | None = None,
    ) -> None:
        self._account_label = account_label
        self._auth_exc = auth_exc
        self._auth_silent_exc = auth_silent_exc
        self._fetch_exc = fetch_exc
        self._send_exc = send_exc
        self._verify_exc = verify_exc
        self._delete_exc = delete_exc
        self._restore_exc = restore_exc
        self._move_to_trash_exc = move_to_trash_exc
        self._fetch_messages_metadata_exc = fetch_messages_metadata_exc
        self._update_read_status_exc = update_read_status_exc
        self._move_to_spam_exc = move_to_spam_exc
        self._restore_from_spam_exc = restore_from_spam_exc
        self._move_to_archive_exc = move_to_archive_exc
        self._restore_from_archive_exc = restore_from_archive_exc
        self._fetch_content_exc = fetch_content_exc
        self._email_content = email_content or EmailContent(html_body=None, text_body=None)
        self._create_draft_exc = create_draft_exc
        self._create_draft_return = create_draft_return
        self._update_draft_exc = update_draft_exc
        self._update_draft_return = update_draft_return
        self._delete_draft_exc = delete_draft_exc
        self._fetch_drafts_exc = fetch_drafts_exc
        self._fetch_drafts_return = list(fetch_drafts_return or [])
        self._send_draft_exc = send_draft_exc
        self._send_draft_return = send_draft_return
        # Background backfill. ``fetch_backfill_pages`` is a queue popped one per
        # call so a test can walk a paginated backfill (each element is a
        # BackfillPage); it takes precedence over the single ``*_return``.
        self._capture_backfill_anchor_return = capture_backfill_anchor_return
        self._capture_backfill_anchor_exc = capture_backfill_anchor_exc
        self._fetch_backfill_page_return = fetch_backfill_page_return
        self._fetch_backfill_pages = list(fetch_backfill_pages or [])
        self._fetch_backfill_page_exc = fetch_backfill_page_exc
        self._list_message_attachments_exc = list_message_attachments_exc
        self._fetch_attachment_binary_exc = fetch_attachment_binary_exc
        self._send_draft_with_attachments_exc = send_draft_with_attachments_exc
        self._set_favorite_exc = set_favorite_exc
        self._list_favorite_ids_exc = list_favorite_ids_exc
        self._list_favorite_ids_return = list(list_favorite_ids_return or [])
        self._list_favorite_candidates_return = list_favorite_candidates_return
        self._fetch_reply_context_exc = fetch_reply_context_exc
        self._fetch_reply_context_return = fetch_reply_context_return
        self._fetch_conversation_exc = fetch_conversation_exc
        self._fetch_conversation_return = fetch_conversation_return
        self._list_message_attachments_return = list_message_attachments_return
        self._fetch_attachment_binary_return = fetch_attachment_binary_return
        self._send_draft_with_attachments_return = send_draft_with_attachments_return
        self._metadata = list(metadata or [])
        self._sync_cursor_return = sync_cursor_return
        self._auth_return = auth_return
        self._auth_silent_return = auth_silent_return
        self._deletes = list(deletes or [])
        self._label_updates = list(label_updates or [])
        self._existing_message_ids = set(existing_message_ids or [])
        self._is_full_sync = is_full_sync
        self._delete_return = delete_return
        self._restore_return = restore_return
        self._move_to_trash_return = move_to_trash_return
        self._move_to_archive_return = move_to_archive_return
        self._fetch_messages_metadata_return = fetch_messages_metadata_return
        self.begin_interactive_auth_calls = 0
        self.complete_interactive_auth_calls = 0
        self.authenticate_silent_calls = 0
        self.fetch_calls = 0
        self.verify_calls = 0
        self.delete_calls = 0
        self.restore_calls = 0
        self.move_to_trash_calls = 0
        self.fetch_messages_metadata_calls = 0
        self.fetch_content_with_attachments_calls: list[str] = []
        self.update_read_status_calls: list[tuple[list[str], bool]] = []
        self.move_to_spam_calls: list[list[str]] = []
        self.restore_from_spam_calls: list[list[str]] = []
        self.move_to_archive_calls: list[list[str]] = []
        self.restore_from_archive_calls: list[list[str]] = []
        self.sent_emails: list[tuple[str, str, list[str]]] = []
        self.create_draft_calls: list[tuple[list[str], list[str], list[str], str, str]] = []
        self.update_draft_calls: list[tuple[str, list[str], list[str], list[str], str, str]] = []
        self.delete_draft_calls: list[str] = []
        self.fetch_drafts_calls = 0
        self.send_draft_calls: list[str] = []
        self.list_message_attachments_calls: list[str] = []
        self.fetch_attachment_binary_calls: list[tuple[str, AttachmentMetadata]] = []
        self.send_draft_with_attachments_calls: list[
            tuple[str, list[str], list[str], list[str], str, str, list[DraftAttachmentInput]]
        ] = []
        self.set_favorite_calls: list[tuple[str, bool]] = []
        self.list_favorite_ids_calls = 0
        self.list_favorite_candidates_calls = 0
        # Reply / Forward bookkeeping. ``create_draft_reply_kwargs`` and
        # ``send_draft_with_attachments_reply_kwargs`` are populated on
        # every call so a test can assert that the reply / forward
        # kwargs were propagated from the service layer all the way to
        # the provider client. Each entry is a dict of the new optional
        # kwargs (``thread_id`` / ``in_reply_to`` / ``references`` /
        # ``reply_to_message_id`` / ``reply_kind`` / ``original_subject``
        # for create_draft, and the subset that send accepts).
        self.fetch_reply_context_calls: list[str] = []
        # Conversation viewer. Records each ``thread_id`` passed to
        # ``fetch_conversation`` so a test can assert the service derived the
        # right thread from the base message row.
        self.fetch_conversation_calls: list[str] = []
        # Background backfill bookkeeping. ``capture_backfill_anchor_calls`` is a
        # counter; ``fetch_backfill_page_calls`` records each ``(cursor,
        # page_size)`` so a test can assert the worker paginates with the
        # returned ``next_cursor`` and clamps ``page_size`` to what remains.
        self.capture_backfill_anchor_calls = 0
        self.fetch_backfill_page_calls: list[tuple[str | None, int]] = []
        self.create_draft_reply_kwargs: list[dict] = []
        self.send_draft_with_attachments_reply_kwargs: list[dict] = []
        self.deleted_message_ids: list[str] = []
        self.restored_items: list[dict] = []
        self.trashed_items: list[dict[str, str]] = []
        self.last_app_credentials = None
        self.last_user_tokens = None
        self.last_sync_cursor = None
        self.last_redirect_uri = None
        self.last_flow_state = None
        self.last_auth_code = None

    def begin_interactive_auth(self, app_credentials=None, redirect_uri=None) -> dict:
        self.begin_interactive_auth_calls += 1
        self.last_app_credentials = app_credentials
        self.last_redirect_uri = redirect_uri
        if self._auth_exc:
            raise self._auth_exc
        return {
            "authorization_url": "https://provider.example/authorize?state=fake-state",
            "state": "fake-state",
            "flow_state": {"fake": True},
        }

    def complete_interactive_auth(self, app_credentials=None, flow_state=None, code=None) -> dict:
        self.complete_interactive_auth_calls += 1
        self.last_app_credentials = app_credentials
        self.last_flow_state = flow_state
        self.last_auth_code = code
        if self._auth_exc:
            raise self._auth_exc
        return self._auth_return

    def authenticate_silent(self, app_credentials=None, user_tokens=None) -> dict | None:
        self.authenticate_silent_calls += 1
        self.last_app_credentials = app_credentials
        self.last_user_tokens = user_tokens
        if self._auth_silent_exc:
            raise self._auth_silent_exc
        return self._auth_silent_return

    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        self.fetch_calls += 1
        self.last_sync_cursor = sync_cursor
        if self._fetch_exc:
            raise self._fetch_exc
        return SyncResult(
            upserts=list(self._metadata),
            new_cursor=self._sync_cursor_return,
            deletes=list(self._deletes),
            label_updates=list(self._label_updates),
            is_full_sync=self._is_full_sync,
        )

    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        self.verify_calls += 1
        if self._verify_exc:
            raise self._verify_exc
        return [mid for mid in message_ids if mid in self._existing_message_ids]

    def delete_messages(self, message_ids: list[str]) -> list[str]:
        self.delete_calls += 1
        if self._delete_exc:
            raise self._delete_exc
        result = self._delete_return if self._delete_return is not None else list(message_ids)
        self.deleted_message_ids.extend(result)
        return result

    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        self.restore_calls += 1
        if self._restore_exc:
            raise self._restore_exc
        result = self._restore_return if self._restore_return is not None else {k: k for k in items}
        self.restored_items.append(dict(items))
        return result

    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        self.fetch_messages_metadata_calls += 1
        if self._fetch_messages_metadata_exc:
            raise self._fetch_messages_metadata_exc
        if self._fetch_messages_metadata_return is not None:
            return list(self._fetch_messages_metadata_return)
        return []

    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        self.move_to_trash_calls += 1
        if self._move_to_trash_exc:
            raise self._move_to_trash_exc
        result = self._move_to_trash_return if self._move_to_trash_return is not None else {mid: mid for mid in message_ids}
        self.trashed_items.append(dict(result))
        return result

    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        self.update_read_status_calls.append((list(message_ids), is_read))
        if self._update_read_status_exc:
            raise self._update_read_status_exc
        return list(message_ids)

    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        self.move_to_spam_calls.append(list(message_ids))
        if self._move_to_spam_exc:
            raise self._move_to_spam_exc
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in message_ids]

    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        self.restore_from_spam_calls.append(list(message_ids))
        if self._restore_from_spam_exc:
            raise self._restore_from_spam_exc
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in message_ids]

    def move_to_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        self.move_to_archive_calls.append(list(message_ids))
        if self._move_to_archive_exc:
            raise self._move_to_archive_exc
        if self._move_to_archive_return is not None:
            return list(self._move_to_archive_return)
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in message_ids]

    def restore_from_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        self.restore_from_archive_calls.append(list(message_ids))
        if self._restore_from_archive_exc:
            raise self._restore_from_archive_exc
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in message_ids]

    def send_email(self, subject: str, body: str, recipients: list[str]) -> EmailMetadata:
        if self._send_exc:
            raise self._send_exc
        self.sent_emails.append((subject, body, list(recipients)))
        return build_metadata(
            provider_message_id="sent_m1",
            subject=subject,
            box="SENT",
            is_read=True,
        )

    def fetch_content_with_attachments(
        self, provider_message_id: str,
    ) -> tuple[EmailContent, list[AttachmentMetadata], dict[str, str]]:
        # Unified body+attachments read. Reuses the SAME injection points as
        # the two methods it replaces: ``fetch_content_exc`` drives the
        # failure path (so the existing 502 tests keep working), and the
        # attachments / cid_map come from ``list_message_attachments_return``
        # (so the cache-miss attachment-discovery tests keep working).
        self.fetch_content_with_attachments_calls.append(provider_message_id)
        if self._fetch_content_exc:
            raise self._fetch_content_exc
        if self._list_message_attachments_return is not None:
            attachments, cid_map = self._list_message_attachments_return
        else:
            attachments, cid_map = [], {}
        return self._email_content, attachments, cid_map

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
        self.create_draft_calls.append(
            (list(to_recipients), list(cc_recipients), list(bcc_recipients), subject, body)
        )
        # Mirror the reply / forward kwargs into their own list so a
        # test can assert exactly what was propagated to the provider
        # client without having to reshape ``create_draft_calls``.
        self.create_draft_reply_kwargs.append({
            "thread_id": thread_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "reply_to_message_id": reply_to_message_id,
            "reply_kind": reply_kind,
            "original_subject": original_subject,
        })
        if self._create_draft_exc:
            raise self._create_draft_exc
        if self._create_draft_return is not None:
            return self._create_draft_return
        return DraftMetadata(
            provider_draft_id="fake_draft_1",
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=DEFAULT_RECEIVED_AT,
            updated_at=DEFAULT_RECEIVED_AT,
        )

    def update_draft(
        self,
        provider_draft_id: str,
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        self.update_draft_calls.append(
            (
                provider_draft_id,
                list(to_recipients),
                list(cc_recipients),
                list(bcc_recipients),
                subject,
                body,
            )
        )
        if self._update_draft_exc:
            raise self._update_draft_exc
        if self._update_draft_return is not None:
            return self._update_draft_return
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=DEFAULT_RECEIVED_AT,
            updated_at=DEFAULT_RECEIVED_AT,
        )

    def delete_draft(self, provider_draft_id: str) -> None:
        self.delete_draft_calls.append(provider_draft_id)
        if self._delete_draft_exc:
            raise self._delete_draft_exc

    def fetch_drafts(self) -> list[DraftMetadata]:
        self.fetch_drafts_calls += 1
        if self._fetch_drafts_exc:
            raise self._fetch_drafts_exc
        return list(self._fetch_drafts_return)

    def send_draft(self, provider_draft_id: str) -> EmailMetadata:
        self.send_draft_calls.append(provider_draft_id)
        if self._send_draft_exc:
            raise self._send_draft_exc
        if self._send_draft_return is not None:
            return self._send_draft_return
        return build_metadata(
            provider_message_id=f"sent_{provider_draft_id}",
            subject="",
            box="SENT",
            is_read=True,
        )

    def list_message_attachments(
        self,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        self.list_message_attachments_calls.append(provider_message_id)
        if self._list_message_attachments_exc:
            raise self._list_message_attachments_exc
        if self._list_message_attachments_return is not None:
            return self._list_message_attachments_return
        return [], {}

    def fetch_attachment_binary(
        self,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        self.fetch_attachment_binary_calls.append((provider_message_id, attachment))
        if self._fetch_attachment_binary_exc:
            raise self._fetch_attachment_binary_exc
        if self._fetch_attachment_binary_return is not None:
            return self._fetch_attachment_binary_return
        return AttachmentBinary(
            mime_type=attachment.mime_type or "application/octet-stream",
            filename=attachment.filename,
            data=b"fake-bytes",
            size=len(b"fake-bytes"),
        )

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
        self.send_draft_with_attachments_calls.append(
            (
                provider_draft_id,
                list(to_recipients),
                list(cc_recipients),
                list(bcc_recipients),
                subject,
                body,
                list(attachments),
            )
        )
        self.send_draft_with_attachments_reply_kwargs.append({
            "in_reply_to": in_reply_to,
            "references": references,
            "thread_id": thread_id,
        })
        if self._send_draft_with_attachments_exc:
            raise self._send_draft_with_attachments_exc
        if self._send_draft_with_attachments_return is not None:
            return self._send_draft_with_attachments_return
        sent_meta = build_metadata(
            provider_message_id=f"sent_{provider_draft_id}",
            subject=subject,
            box="SENT",
            is_read=True,
        )
        return sent_meta, []

    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        self.set_favorite_calls.append((provider_message_id, is_favorite))
        if self._set_favorite_exc:
            raise self._set_favorite_exc

    def list_favorite_ids(self) -> list[str]:
        self.list_favorite_ids_calls += 1
        if self._list_favorite_ids_exc:
            raise self._list_favorite_ids_exc
        return list(self._list_favorite_ids_return)

    def list_favorite_candidates(self) -> list[FavoriteCandidate]:
        """Return the injected enriched candidates, or defer to the ABC
        default (which wraps ``list_favorite_ids()`` — reusing its exc/return
        injection unchanged).

        Tests exercising the Outlook per-endpoint id drift reconciliation
        inject ``list_favorite_candidates_return`` directly; tests that only
        care about the raw-id path keep using ``list_favorite_ids_return``.
        """
        self.list_favorite_candidates_calls += 1
        if self._list_favorite_candidates_return is not None:
            return list(self._list_favorite_candidates_return)
        return super().list_favorite_candidates()

    def fetch_reply_context(self, provider_message_id: str) -> ReplyContext:
        """Return the injected ``ReplyContext`` (or a benign default).

        The default value is intentionally empty so tests that exercise
        unrelated flows don't need to provide one. Tests that exercise
        the reply / forward surface should always inject
        ``fetch_reply_context_return`` to lock the payload.
        """
        self.fetch_reply_context_calls.append(provider_message_id)
        if self._fetch_reply_context_exc:
            raise self._fetch_reply_context_exc
        if self._fetch_reply_context_return is not None:
            return self._fetch_reply_context_return
        return ReplyContext(
            provider_message_id=provider_message_id,
            thread_id="",
            from_email="",
            from_name="",
            reply_to=[],
            to_recipients=[],
            cc_recipients=[],
            subject="",
            body_html=None,
            body_text=None,
            received_at=DEFAULT_RECEIVED_AT,
            message_id="",
            references="",
            box="ALL_MAIL",
        )

    def fetch_conversation(self, thread_id: str) -> list[ConversationMessage]:
        """Return the injected conversation members (or an empty list).

        Records the ``thread_id`` so a test can assert the service derived
        the right thread from the base message row. Tests that exercise the
        conversation viewer inject ``fetch_conversation_return`` (deliberately
        unsorted, to verify the service sorts ascending) or
        ``fetch_conversation_exc``.
        """
        self.fetch_conversation_calls.append(thread_id)
        if self._fetch_conversation_exc:
            raise self._fetch_conversation_exc
        if self._fetch_conversation_return is not None:
            return list(self._fetch_conversation_return)
        return []

    def capture_backfill_anchor(self) -> str:
        """Return the injected backfill anchor (or a benign default).

        The real ABC leaves this ``raise NotImplementedError``; the fake
        overrides it so the manager-delegation tests (and any worker test that
        drives the fake through the real EmailManager) can exercise the happy
        path. Inject ``capture_backfill_anchor_exc`` for the failure path.
        """
        self.capture_backfill_anchor_calls += 1
        if self._capture_backfill_anchor_exc:
            raise self._capture_backfill_anchor_exc
        return self._capture_backfill_anchor_return

    def fetch_backfill_page(self, cursor: str | None, page_size: int) -> BackfillPage:
        """Return the next injected backfill page (queue first, then single).

        Records ``(cursor, page_size)`` so a test can assert the paginating
        caller forwards the previous page's ``next_cursor`` and clamps
        ``page_size``. Falls back to a single exhausting page built from
        ``metadata`` (``next_cursor=None``) when nothing was injected.
        """
        self.fetch_backfill_page_calls.append((cursor, page_size))
        if self._fetch_backfill_page_exc:
            raise self._fetch_backfill_page_exc
        if self._fetch_backfill_pages:
            return self._fetch_backfill_pages.pop(0)
        if self._fetch_backfill_page_return is not None:
            return self._fetch_backfill_page_return
        return BackfillPage(upserts=list(self._metadata), next_cursor=None)

    def get_account_label(self) -> str:
        return self._account_label
