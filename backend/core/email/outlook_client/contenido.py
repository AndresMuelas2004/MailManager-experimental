"""Contenido Outlook: cuerpo+adjuntos unificado, conversaciones, contexto de respuesta y binarios."""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from ..email_client import (
    AttachmentBinary,
    AttachmentMetadata,
    ConversationMessage,
    EmailContent,
    ReplyContext,
)
from ..errors import (
    CoreError,
    EmailAttachmentDownloadFailed,
    EmailAttachmentNotFound,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
    EmailReplyContextFetchError,
)
from ..helpers import (
    find_referenced_cids,
    inline_cid_images,
    normalize_cid,
    resolve_attachment_mime_type,
)
from .transporte import (
    GRAPH_BASE_URL,
    _OUTLOOK_RETRY_DELAYS_SECONDS,
    _PREFER_IMMUTABLE_HEADERS,
    _parse_graph_datetime,
    _retry_after_seconds,
)

logger = logging.getLogger(__name__)


# Conversation view ($filter=conversationId): bodies are fetched lazily
# per message via fetch_email_content, so the select stays lightweight.
# Adds ``sentDateTime`` (ordering fallback for Sent items) and ``flag``
# (favourite state) on top of the bootstrap fields. ``isDraft`` is selected
# ONLY to filter drafts out — the mailbox-wide ``$filter=conversationId``
# scope includes the Drafts folder (unlike the sync's folder deltas), and a
# draft reply leaking through here would be shown as a thread message AND
# persisted into ``email_metadata`` by the viewer's lazy-sync, violating the
# "drafts never enter email_metadata" invariant (drafts live in ``drafts``).
_CONVERSATION_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,"
    "receivedDateTime,sentDateTime,isRead,parentFolderId,flag,isDraft"
)


class OutlookContenidoMixin:
    """Metodos de contenido/adjuntos de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def fetch_conversation(self, thread_id: str) -> list[ConversationMessage]:
        """Fetch every message of an Outlook conversation (metadata + state, NO body).

        Filters the whole mailbox with ``$filter=conversationId eq
        '<id>'`` — the scope of ``/me/messages`` spans Sent Items, Junk
        and Deleted Items, so the thread is reconstructed across folders.
        The ``conversationId`` is base64 (contains ``+`` / ``/`` / ``=``)
        and is percent-encoded exactly once before being wrapped in the
        single quotes Graph requires. ``$orderby`` is deliberately
        omitted — combining it with ``$filter=conversationId`` returns
        ``400 InefficientFilter`` — so messages are sorted in the client
        by ``receivedDateTime`` (falling back to ``sentDateTime`` for
        Sent items that lack it). Each message is parsed with the same
        :py:meth:`_parse_graph_message` used by sync and enriched with
        ``flag.flagStatus`` for ``is_favorite``.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_conversation requires authentication.")

        folder_id_to_box = self._resolve_special_folder_ids()

        encoded_conversation_id = urllib.parse.quote(thread_id, safe="")
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            f"?$filter=conversationId%20eq%20'{encoded_conversation_id}'"
            f"&$select={_CONVERSATION_SELECT_FIELDS}"
            "&$top=50"
        )

        messages: list[ConversationMessage] = []
        while url:
            response = self._graph_request(
                "GET", url, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            for msg in response.get("value", []) or []:
                # Drafts belong to the ``drafts`` table, never to the viewer
                # nor to ``email_metadata`` (the lazy-sync persists whatever
                # is returned here). Mirrors the bootstrap's isDraft filter.
                if msg.get("isDraft"):
                    continue
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                try:
                    # ``received_at`` (incl. the sentDateTime fallback for
                    # Sent items) is derived INSIDE _parse_graph_message so
                    # every endpoint shares one chain — load-bearing for the
                    # id-reconciliation identity.
                    meta = self._parse_graph_message(msg, box)
                    is_favorite = (msg.get("flag") or {}).get("flagStatus") == "flagged"
                    messages.append(
                        ConversationMessage(
                            provider_message_id=meta.provider_message_id,
                            thread_id=meta.thread_id,
                            from_email=meta.from_email,
                            from_name=meta.from_name,
                            subject=meta.subject,
                            received_at=meta.received_at,
                            is_read=meta.is_read,
                            is_favorite=is_favorite,
                            box=meta.box,
                            to_email=meta.to_email,
                            to_name=meta.to_name,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Outlook fetch_conversation: skipping unparseable message %s: %s",
                        msg.get("id", "?"), exc,
                    )
            url = response.get("@odata.nextLink")

        messages.sort(key=lambda m: (m.received_at, m.provider_message_id))
        return messages

    def fetch_content_with_attachments(
        self, provider_message_id: str,
    ) -> tuple[EmailContent, list[AttachmentMetadata], dict[str, str]]:
        """Body + attachments + inline ``cid_map`` for a single Outlook message.

        Fuses what ``fetch_email_content`` + ``list_message_attachments``
        did with two body GETs into one: a single ``GET /me/messages/{id}``
        for the body, then ``_classify_attachments`` (one ``GET .../attachments``)
        when the body is HTML.

        ``_classify_attachments`` is NOT gated on ``hasAttachments`` (unlike
        the old ``fetch_email_content``): ``hasAttachments`` is ``false``
        when a message carries ONLY inline images, so a guard there would
        skip resolving the referenced ``cid:`` images and break the render
        (D-13). It is best-effort internally (a failed attachments GET
        returns ``({}, [])`` and only logs), so classifying always adds no
        hard failure point. A non-HTML body is NOT classified — it carries
        no ``cid:`` references and no inline-image semantics — matching the
        previous behaviour (returns ``text_body`` with empty attachments).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_content_with_attachments requires authentication."
            )
        try:
            escaped_id = urllib.parse.quote(provider_message_id, safe="")
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select=body,hasAttachments",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            body = response.get("body", {})
            content_type = body.get("contentType", "").lower()
            content = body.get("content")
            if content_type != "html":
                return EmailContent(html_body=None, text_body=content), [], {}
            cid_map, downloadable = self._classify_attachments(
                escaped_id, content, provider_message_id=provider_message_id,
            )
            if content and cid_map:
                content = inline_cid_images(content, cid_map)
            return EmailContent(html_body=content, text_body=None), downloadable, cid_map
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected fetch_content_with_attachments error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_reply_context(self, provider_message_id: str) -> ReplyContext:
        """Single ``GET /me/messages/{id}`` covering every Reply / Forward
        input field, with provider parsing isolated in
        :py:meth:`_reply_context_from_graph_message`.

        ``$select`` enumerates only the fields the composer needs:
        ``from``, ``toRecipients``, ``ccRecipients``, ``replyTo``,
        ``subject``, ``body``, ``internetMessageId``,
        ``internetMessageHeaders``, ``receivedDateTime``,
        ``conversationId``, ``parentFolderId``, ``hasAttachments``.

        ``parentFolderId`` is mapped to a box via the existing
        :py:meth:`_resolve_special_folder_ids` helper so the
        self-reply override ("I replied to my own Sent message") can
        be derived in the service layer.

        Errors from Graph are wrapped as
        :py:class:`EmailReplyContextFetchError` so the service maps
        them to the reply-specific ``EmailReplyContextError`` (HTTP
        502).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_reply_context requires authentication."
            )
        escaped_id = urllib.parse.quote(provider_message_id, safe="")
        select_fields = (
            "from,sender,toRecipients,ccRecipients,replyTo,subject,body,"
            "internetMessageId,internetMessageHeaders,receivedDateTime,"
            "conversationId,parentFolderId,hasAttachments"
        )
        try:
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select={select_fields}",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError as exc:
            raise EmailReplyContextFetchError(
                f"Failed to fetch reply context for Outlook message {provider_message_id}: {exc.message}",
                detail={"reason": "provider_fetch_failed"},
            ) from exc
        except CoreError:
            # Any other CoreError (e.g. a reply-context error from a future
            # helper inside the try) passes through untouched instead of being
            # re-wrapped by the generic handler below (core/CLAUDE.md §5).
            raise
        except Exception as exc:
            raise EmailReplyContextFetchError(
                f"Outlook unexpected fetch_reply_context error ({type(exc).__name__}): {exc}",
                detail={"reason": "provider_fetch_failed"},
            ) from exc

        # Best-effort folder resolution. A provider failure here just
        # collapses to ``ALL_MAIL`` — the self-reply override is a UX
        # nicety, not a correctness invariant. Narrowed to the provider
        # error (core/CLAUDE.md §5): an unexpected exception must not be
        # silently swallowed into the wrong box.
        try:
            folder_id_to_box = self._resolve_special_folder_ids()
        except EmailExternalAPIError as exc:  # pragma: no cover — defensive
            logger.warning(
                "Outlook reply context: folder resolution failed (%s): %s",
                type(exc).__name__, exc,
            )
            folder_id_to_box = {}
        parent_folder_id = response.get("parentFolderId") or ""
        box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")

        return self._reply_context_from_graph_message(
            provider_message_id=provider_message_id, message=response, box=box,
        )

    @staticmethod
    def _emails_from_recipients(recipients: Any) -> list[str]:
        """Decompose a Graph ``recipients`` list into plain email strings.

        Graph returns ``[{"emailAddress": {"address": "...", "name": "..."}}, ...]``.
        We surface a flat list of addresses for symmetry with
        :py:meth:`GmailClient._split_address_header` and to keep the
        :py:class:`ReplyContext` shape provider-agnostic.
        """
        out: list[str] = []
        if not isinstance(recipients, list):
            return out
        for entry in recipients:
            if not isinstance(entry, dict):
                continue
            addr_obj = entry.get("emailAddress") or {}
            if not isinstance(addr_obj, dict):
                continue
            addr = (addr_obj.get("address") or "").strip()
            if addr and "@" in addr:
                out.append(addr)
        return out

    @staticmethod
    def _extract_email_address(recipient: Any) -> tuple[str, str]:
        """Return ``(email, name)`` from a Graph ``recipient`` object.

        Graph wraps single-recipient fields (``from``, ``sender``) in
        the shape ``{"emailAddress": {"address": "...", "name": "..."}}``.
        Returns ``("", "")`` when the structure is missing or invalid.
        """
        if not isinstance(recipient, dict):
            return "", ""
        addr_obj = recipient.get("emailAddress") or {}
        if not isinstance(addr_obj, dict):
            return "", ""
        addr = (addr_obj.get("address") or "").strip()
        name = (addr_obj.get("name") or "").strip()
        return addr, name

    @staticmethod
    def _first_recipient_from_graph_recipients(
        recipients: Any,
    ) -> tuple[str, str]:
        """Extract ``(name, email)`` of the first valid Graph recipient.

        Symmetric with :py:meth:`_emails_from_recipients` (same Graph
        shape parsing) but preserves the display name for the leading
        entry — used to populate ``EmailMetadata.to_email`` / ``to_name``
        during sync. Returns ``("", "")`` when no recipient carries a
        valid ``@`` address.
        """
        if not isinstance(recipients, list):
            return "", ""
        for entry in recipients:
            if not isinstance(entry, dict):
                continue
            addr_obj = entry.get("emailAddress") or {}
            if not isinstance(addr_obj, dict):
                continue
            addr = (addr_obj.get("address") or "").strip()
            if not addr or "@" not in addr:
                continue
            name = (addr_obj.get("name") or "").strip()
            return name, addr
        return "", ""

    @staticmethod
    def _references_from_internet_headers(headers: Any) -> str:
        """Extract the raw ``References`` value from ``internetMessageHeaders``.

        The Graph property is a list of ``{name, value}`` records. We
        match ``References`` case-insensitively (RFC 5322 header names
        are case-insensitive). A missing header collapses to ``""``.
        """
        if not isinstance(headers, list):
            return ""
        for entry in headers:
            if not isinstance(entry, dict):
                continue
            if (entry.get("name") or "").lower() == "references":
                return str(entry.get("value") or "")
        return ""

    def _reply_context_from_graph_message(
        self,
        *,
        provider_message_id: str,
        message: dict[str, Any],
        box: str,
    ) -> ReplyContext:
        """Pure-shape conversion from Graph ``message`` JSON to ``ReplyContext``.

        Split from :py:meth:`fetch_reply_context` so the HTTP layer
        and the parsing layer can be tested independently (fakes that
        feed pre-shaped dicts don't need to mock the Graph stack).
        """
        from_email, from_name = self._extract_email_address(message.get("from"))
        if not from_email:
            # Graph occasionally omits ``from`` (delegated mailboxes,
            # certain on-behalf-of sends). ``sender`` is documented as
            # the actual sending mailbox and is always populated for
            # received messages — fall back to it so the composer can
            # still seed ``to_recipients`` for Reply.
            from_email, sender_name = self._extract_email_address(message.get("sender"))
            if not from_name:
                from_name = sender_name

        body_section = message.get("body") or {}
        content_type = (body_section.get("contentType") or "").lower()
        content = body_section.get("content")
        if content_type == "html":
            html_body = content
            text_body = None
        else:
            html_body = None
            text_body = content

        message_id_raw = (message.get("internetMessageId") or "").strip().strip("<>")
        references = self._references_from_internet_headers(
            message.get("internetMessageHeaders"),
        )
        received_at = _parse_graph_datetime(message.get("receivedDateTime"))

        return ReplyContext(
            provider_message_id=provider_message_id,
            thread_id=str(message.get("conversationId") or ""),
            from_email=from_email,
            from_name=from_name,
            reply_to=self._emails_from_recipients(message.get("replyTo")),
            to_recipients=self._emails_from_recipients(message.get("toRecipients")),
            cc_recipients=self._emails_from_recipients(message.get("ccRecipients")),
            subject=str(message.get("subject") or ""),
            body_html=html_body,
            body_text=text_body,
            received_at=received_at,
            message_id=message_id_raw,
            references=references,
            box=box,
        )

    def list_message_attachments(
        self,
        provider_message_id: str,
    ) -> tuple[list[AttachmentMetadata], dict[str, str]]:
        """List downloadable attachments + inline cid_map for an Outlook message.

        Single ``GET /me/messages/{id}`` (with body) plus ``GET .../attachments``
        — the body call is needed only to compute referenced CIDs (D-13).
        We could in theory share the call with ``fetch_email_content`` but
        in the cache-aside flow the service invokes both; the duplication
        is bounded (two cheap GETs) and the call sites stay simple.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook list_message_attachments requires authentication."
            )
        escaped_id = urllib.parse.quote(provider_message_id, safe="")
        try:
            body_response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_id}?$select=body",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        body_section = body_response.get("body") or {}
        html_body = (
            body_section.get("content")
            if (body_section.get("contentType") or "").lower() == "html"
            else None
        )
        cid_map, downloadable = self._classify_attachments(
            escaped_id, html_body, provider_message_id=provider_message_id,
        )
        return downloadable, cid_map

    def _classify_attachments(
        self,
        escaped_message_id: str,
        html_body: str | None,
        *,
        provider_message_id: str | None = None,
    ) -> tuple[dict[str, str], list[AttachmentMetadata]]:
        """Walk Outlook's attachment list applying the D-13 strict rule.

        Returns ``(cid_map, downloadable)`` — the same shape as Gmail's
        ``_classify_attachments`` so the service code is symmetrical:
        - ``cid_map`` holds inline images whose ``contentId`` IS
          referenced by ``html_body``.
        - ``downloadable`` lists every other attachment the user should
          see (``isInline=false`` or inline-marked-but-unreferenced).

        The Outlook ``attachment.id`` is used as ``provider_attachment_id``
        because the call sends ``Prefer: IdType="ImmutableId"`` per
        request (the cache key per D-06b-clave is stable while the
        message stays in the same mailbox).
        """
        try:
            response = self._graph_request(
                "GET",
                f"{GRAPH_BASE_URL}/me/messages/{escaped_message_id}/attachments"
                "?$select=id,name,contentType,contentBytes,size,contentId,isInline",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError as exc:
            # Intentional best-effort: an attachments listing failure must
            # not abort the whole content fetch. Returning empty lists
            # produces a body with no inline images and no attachments
            # listed — the user sees the message text, and a retry of the
            # content endpoint will pick the lists up once Graph recovers.
            logger.warning("Outlook attachments fetch failed: %s", exc)
            return {}, []
        except Exception as exc:
            # Same best-effort path for any unexpected failure (parse error,
            # Graph schema drift, etc.). Logged with the type so silent
            # regressions remain observable.
            logger.warning(
                "Outlook attachments unexpected error (%s): %s",
                type(exc).__name__, exc,
            )
            return {}, []

        referenced_cids = find_referenced_cids(html_body)
        cid_map: dict[str, str] = {}
        downloadable: list[AttachmentMetadata] = []
        position_counter = 0

        for attachment in response.get("value") or []:
            odata_type = (attachment.get("@odata.type") or "").lower()
            if odata_type and "fileattachment" not in odata_type:
                # Item / reference attachments are surfaced as
                # downloadables for completeness; binary fetch for
                # referenceAttachment is out-of-scope MVP (returns 405
                # at the provider). The user sees them in the list with
                # the provider's declared mime/size.
                pass
            cid_raw = (attachment.get("contentId") or "").strip().strip("<>").strip() or None
            name = attachment.get("name") or "attachment"
            # B-MIME + B-OUTLOOK-LOWER: resolve the type through the shared
            # helper. A generic declared type (``application/octet-stream``)
            # is overridden by the type inferred from the filename extension;
            # a specific declared type is kept verbatim, dropping the previous
            # forced ``.lower()`` that made Outlook diverge from Gmail.
            content_type = resolve_attachment_mime_type(name, attachment.get("contentType"))
            content_bytes_b64 = attachment.get("contentBytes")
            is_inline = bool(attachment.get("isInline"))
            attachment_id = str(attachment.get("id") or "")
            size = int(attachment.get("size") or 0)

            # ``find_referenced_cids`` returns normalised entries; normalise
            # the Graph ``contentId`` on both the membership check and the
            # ``cid_map`` key so header/HTML case or percent-encoding
            # mismatches still resolve (``inline_cid_images`` normalises the
            # HTML-side reference on lookup).
            if (
                is_inline
                and cid_raw
                and normalize_cid(cid_raw) in referenced_cids
                and content_bytes_b64
                and content_type.lower().startswith("image/")
            ):
                cid_map[normalize_cid(cid_raw)] = f"data:{content_type};base64,{content_bytes_b64}"
                continue

            downloadable.append(
                AttachmentMetadata(
                    provider_message_id=provider_message_id or "",
                    part_id=None,
                    provider_attachment_id=attachment_id or None,
                    filename=str(name),
                    mime_type=content_type,
                    size=size,
                    content_id=cid_raw,
                    is_inline=is_inline,
                    position=position_counter,
                )
            )
            position_counter += 1

        return cid_map, downloadable

    def fetch_attachment_binary(
        self,
        provider_message_id: str,
        attachment: AttachmentMetadata,
    ) -> AttachmentBinary:
        """Download a single Outlook attachment binary on demand.

        Uses ``GET /me/messages/{id}/attachments/{att}/$value`` to skip
        the base64 round-trip (recommended in
        ``adjuntos-outlook.md`` § 4.5). Retries transient errors up to 3
        attempts with 1s/2s/4s backoff, honouring ``Retry-After`` when
        Graph emits it. Maps 404/410 to :py:class:`EmailAttachmentNotFound`
        and 403 to :py:class:`EmailAttachmentDownloadFailed` with reason
        ``forbidden`` (D-17).
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_attachment_binary requires authentication."
            )
        if not attachment.provider_attachment_id:
            raise EmailAttachmentNotFound(
                "Outlook attachment is missing provider_attachment_id; cannot resolve $value."
            )
        url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_message_id, safe='')}/attachments/"
            f"{urllib.parse.quote(attachment.provider_attachment_id, safe='')}/$value"
        )
        for attempt, delay in enumerate(_OUTLOOK_RETRY_DELAYS_SECONDS, start=1):
            status, headers, payload = self._graph_request_raw(
                "GET", url, extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
            if 200 <= status < 300:
                return AttachmentBinary(
                    mime_type=attachment.mime_type or "application/octet-stream",
                    filename=attachment.filename,
                    data=payload or b"",
                    size=len(payload or b""),
                )
            if status in (404, 410):
                raise EmailAttachmentNotFound(
                    f"Outlook attachment {attachment.provider_attachment_id} returned {status}.",
                    {"reason": "missing"},
                )
            if status == 403:
                raise EmailAttachmentDownloadFailed(
                    f"Outlook attachment fetch forbidden (HTTP {status}).",
                    {"reason": "forbidden"},
                )
            if status not in (429, 500, 502, 503, 504):
                # Non-retryable status — fail immediately.
                raise EmailAttachmentDownloadFailed(
                    f"Outlook attachment fetch failed (HTTP {status}).",
                    {"reason": "unavailable"},
                )
            # Retryable status: back off and retry, unless this was the last
            # attempt — then fall through to the post-loop raise (now
            # reachable, instead of the previous dead fallback).
            if attempt < len(_OUTLOOK_RETRY_DELAYS_SECONDS):
                retry_after = _retry_after_seconds(headers)
                time.sleep(retry_after if retry_after is not None else delay)
        raise EmailAttachmentDownloadFailed(
            "Outlook attachment fetch exhausted retries without a final response.",
            {"reason": "unavailable"},
        )
