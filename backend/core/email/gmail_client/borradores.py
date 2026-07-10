"""Borradores Gmail: creacion (incluidos Reply/Forward), edicion, borrado y listado."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any

from googleapiclient.errors import HttpError

from ..email_client import DraftAttachmentInput, DraftMetadata
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from ..helpers import build_mime_with_attachments, http_error_detail, plain_text_to_html

logger = logging.getLogger(__name__)


_DRAFTS_MAX_TOTAL = 100


class GmailBorradoresMixin:
    """Metodos de borradores de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    @staticmethod
    def _build_draft_raw_message(
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
        attachments: list[DraftAttachmentInput] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, bytes]:
        """Build the RFC 5322 message for Gmail drafts.create / drafts.update / drafts.send.

        Returns ``(raw_b64url, raw_bytes)``:
        - ``raw_b64url`` is the base64url-encoded form for the JSON
          ``{"message": {"raw": ...}}`` body shape (drafts.create,
          drafts.update, drafts.send simple path).
        - ``raw_bytes`` is the unencoded MIME content used by the
          resumable upload path (``message/rfc822`` body of the PUT).

        Body is HTML. Attachments are appended via
        :py:func:`build_mime_with_attachments` which delegates to
        Python's modern ``email.message.EmailMessage`` API and emits a
        ``multipart/mixed`` wrapping a ``multipart/alternative`` body
        (derived ``text/plain`` + ``text/html``) followed by each
        attachment carrying ``Content-Disposition: attachment``
        (RFC 2231 + RFC 2047 filename encoding).

        ``extra_headers`` carries arbitrary RFC 5322 header injections.
        Used by the Reply / Forward flow to add ``In-Reply-To`` and
        ``References`` so any destination client (Outlook, Apple Mail)
        re-threads even without our Gmail ``threadId``.
        """
        attachment_payloads = [
            {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "data": att.data,
                "content_id": att.content_id,
                "is_inline": att.is_inline,
            }
            for att in (attachments or [])
        ]
        raw_bytes = build_mime_with_attachments(
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            subject=subject or "",
            body=body or "",
            attachments=attachment_payloads,
            extra_headers=extra_headers,
        )
        return base64.urlsafe_b64encode(raw_bytes).decode("utf-8"), raw_bytes

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
        Create a draft in Gmail via users().drafts().create(). All fields
        may be empty; Gmail accepts empty drafts. The body is HTML, sent
        as a ``multipart/alternative`` (derived ``text/plain`` +
        ``text/html``). Attachments are NOT pushed here — they are
        attached during ``send_draft_with_attachments`` (D-07).

        When ``thread_id`` is provided, Gmail's documented triple
        requirement applies: ``threadId`` + ``In-Reply-To`` / ``References``
        + matching ``Subject``. Coherence between those three is
        guaranteed locally by the service layer (which has access to
        the original :py:class:`ReplyContext` and runs
        :py:func:`validate_reply_threading_coherence` before invoking
        us) — so the client trusts the inputs and just propagates them
        to Gmail. ``original_subject`` is accepted to keep the
        contract symmetric with Outlook callers but unused here.

        ``reply_to_message_id`` and ``reply_kind`` are accepted for
        signature symmetry with Outlook but unused by Gmail (no
        ``createReply`` equivalent).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail create_draft requires authentication.")

        del reply_to_message_id, reply_kind, original_subject  # signature symmetry

        extra_headers: dict[str, str] | None = None
        if in_reply_to or references:
            extra_headers = {}
            if in_reply_to:
                extra_headers["In-Reply-To"] = in_reply_to
            if references:
                extra_headers["References"] = references

        raw_message, _ = self._build_draft_raw_message(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
            extra_headers=extra_headers,
        )

        message_payload: dict[str, Any] = {"raw": raw_message}
        if thread_id:
            message_payload["threadId"] = thread_id

        try:
            response = (
                self.service.users()
                .drafts()
                .create(userId="me", body={"message": message_payload})
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to create draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected create_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = response.get("id", "")
        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
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
        """
        Update an existing Gmail draft via users().drafts().update().
        Full-field replacement — Gmail overwrites the entire draft with
        the new MIME message. The Gmail ``draft.id`` is preserved (the
        inner ``message.id`` may change, but we do not store it).
        Body is HTML, sent as a ``multipart/alternative`` (derived
        ``text/plain`` + ``text/html``).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail update_draft requires authentication.")

        raw_message, _ = self._build_draft_raw_message(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        try:
            (
                self.service.users()
                .drafts()
                .update(
                    userId="me",
                    id=provider_draft_id,
                    body={"message": {"raw": raw_message}},
                )
                .execute()
            )
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to update draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected update_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )

    def delete_draft(self, provider_draft_id: str) -> None:
        """Delete a draft in Gmail via users().drafts().delete()."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail delete_draft requires authentication.")

        try:
            self.service.users().drafts().delete(
                userId="me", id=provider_draft_id,
            ).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to delete draft (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected delete_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_drafts(self) -> list[DraftMetadata]:
        """Fetch the most recent Gmail drafts (capped at _DRAFTS_MAX_TOTAL).

        Gmail requires two steps: (1) drafts.list (paginated) to collect up
        to _DRAFTS_MAX_TOTAL (100) draft IDs; (2) drafts.get for each ID to
        retrieve the full Message. Step (2) is executed via
        _execute_batch_get with resource="drafts" — same parallel-batches-of-100
        + 4-retries skeleton used by the email-metadata sync. With a cap of
        100 drafts this degrades to a single batch chunk, but the skeleton
        scales transparently if the cap is raised in the future.

        Gmail's drafts.list API does not support explicit ordering, but
        returns drafts in reverse chronological order by API convention
        (stable in practice, not guaranteed by the official docs).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_drafts requires authentication.")

        # _list_all_draft_ids translates its own provider exceptions into
        # EmailExternalAPIError (core/CLAUDE.md §3), so no wrapper is needed here.
        draft_ids = self._list_all_draft_ids()

        if not draft_ids:
            return []

        raw = self._execute_batch_get(
            draft_ids,
            fmt="full",
            error_context="batch drafts fetch",
            resource="drafts",
        )
        drafts: list[DraftMetadata] = []
        for item in raw.values():
            try:
                drafts.append(self._parse_gmail_draft(item))
            except Exception as exc:
                logger.warning(
                    "Gmail fetch_drafts: skipping unparseable draft %s: %s",
                    item.get("id", "?"), exc,
                )
        return drafts

    def _list_all_draft_ids(self) -> list[str]:
        """Paginated drafts.list — collects up to _DRAFTS_MAX_TOTAL draft IDs.

        Uses maxResults per page capped at both 500 (Gmail's documented max)
        and the remaining quota until _DRAFTS_MAX_TOTAL is reached. Stops
        paginating as soon as the total is hit.

        With the current cap (100), this resolves in a single drafts.list
        call — Gmail returns at most 100 IDs in one page and never follows
        nextPageToken.
        """
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < _DRAFTS_MAX_TOTAL:
            remaining = _DRAFTS_MAX_TOTAL - len(ids)
            page_size = min(500, remaining)
            # Translate the provider exception inside the method that makes the
            # call (core/CLAUDE.md §3), mirroring _list_message_ids — not two
            # frames up in fetch_drafts.
            try:
                response = (
                    self.service.users().drafts()
                    .list(userId="me", maxResults=page_size, pageToken=page_token)
                    .execute()
                )
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail failed to list drafts (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected drafts.list error ({type(exc).__name__}): {exc}"
                ) from exc
            for draft in response.get("drafts", []) or []:
                draft_id = draft.get("id")
                if draft_id:
                    ids.append(draft_id)
                    if len(ids) >= _DRAFTS_MAX_TOTAL:
                        break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return ids

    def _parse_gmail_draft(self, draft_response: dict[str, Any]) -> DraftMetadata:
        """Convert a Gmail drafts.get response (format=full) into DraftMetadata.

        Extracts subject/to/cc/bcc from headers and the body from the
        payload parts, **preferring the ``text/html`` part** so the
        rich-text composer is seeded with HTML. A legacy draft that only
        carries ``text/plain`` (created before the rich-text body, or by
        another client) is converted to HTML via
        :py:func:`plain_text_to_html` so the persisted ``body`` is always
        HTML — the migration fixes the local rows, this fixes any legacy
        draft that re-enters via ``sync_drafts``. Uses ``datetime.now()``
        for created_at/updated_at because Gmail does not expose a stable
        draft timestamp in the Message resource.
        """
        provider_draft_id = str(draft_response.get("id", ""))
        message = draft_response.get("message") or {}
        payload = message.get("payload") or {}
        headers = payload.get("headers") or []

        def _header(name: str) -> str:
            for h in headers:
                if (h.get("name") or "").lower() == name.lower():
                    return h.get("value") or ""
            return ""

        def _parse_addrs(raw: str) -> list[str]:
            # "Name <a@b.com>, Other <c@d.com>" → ["a@b.com", "c@d.com"]
            result: list[str] = []
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                if "<" in part and ">" in part:
                    start = part.find("<") + 1
                    end = part.find(">")
                    if start < end:
                        result.append(part[start:end].strip())
                        continue
                result.append(part)
            return result

        subject = _header("Subject")
        to_recipients = _parse_addrs(_header("To"))
        cc_recipients = _parse_addrs(_header("Cc"))
        bcc_recipients = _parse_addrs(_header("Bcc"))
        # Prefer the text/html part — drafts created/updated by the app
        # now carry an HTML body inside a multipart/alternative. A legacy
        # draft with only a text/plain part (pre-rich-text, or authored by
        # another client) is converted to HTML so the persisted body is
        # always HTML and the composer never shows raw newlines.
        html_body, text_body = self._extract_body_from_payload(payload)
        if html_body is not None:
            body = html_body
        else:
            body = plain_text_to_html(text_body) if text_body is not None else ""
        # The MIME serialization of the body parts appends a single
        # trailing newline that Gmail echoes back verbatim (verified for
        # both the text/plain and the text/html alternative). Strip exactly
        # one terminator (``\r\n`` first, then ``\n`` — order matters so a
        # CRLF does not leave a stray ``\r``) so the round-tripped HTML
        # matches what was composed. Skip the strip for a legacy body that
        # was just wrapped by ``plain_text_to_html`` (it ends in
        # ``</p>``, not a newline), so the guard is harmless there.
        if body.endswith("\r\n"):
            body = body[:-2]
        elif body.endswith("\n"):
            body = body[:-1]

        now = datetime.now(timezone.utc)
        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )
