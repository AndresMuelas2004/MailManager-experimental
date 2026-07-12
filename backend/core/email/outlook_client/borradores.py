"""Borradores Outlook: creacion (createReply*/createForward incluidos), edicion, borrado y listado."""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from ..email_client import DraftMetadata
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError, EmailRecipientsMissingError
from ..helpers import flatten_html_document, plain_text_to_html
from .transporte import GRAPH_BASE_URL, _PREFER_IMMUTABLE_HEADERS, _parse_graph_datetime

logger = logging.getLogger(__name__)


_DRAFTS_PAGE_SIZE = 500
_DRAFTS_MAX_RETRIES = 4
_DRAFTS_RETRY_DELAY = 1.0  # seconds
_DRAFTS_MAX_TOTAL = 500


class OutlookBorradoresMixin:
    """Metodos de borradores de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    @staticmethod
    def _build_draft_graph_payload(
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Build the Graph Message payload used by both create_draft
        (POST /me/messages) and update_draft (PATCH /me/messages/{id}),
        and reused (nested under ``{"message": ...}``) by
        ``createReply`` / ``createReplyAll`` / ``createForward``. Both
        endpoints accept the same shape; update semantically replaces
        every listed field with the new value.

        Body is sent as ``contentType: "HTML"``. The composer emits
        sanitised HTML (negrita, cursiva, listas, enlaces); Graph accepts
        arbitrary HTML on write and re-wraps / sanitises it on read (the
        round-trip is handled by :py:meth:`_parse_outlook_draft`).
        """
        return {
            "subject": subject or "",
            "body": {"contentType": "HTML", "content": body or ""},
            "toRecipients": [{"emailAddress": {"address": r}} for r in to_recipients],
            "ccRecipients": [{"emailAddress": {"address": r}} for r in cc_recipients],
            "bccRecipients": [{"emailAddress": {"address": r}} for r in bcc_recipients],
        }

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
        Create a draft in Outlook.

        - **Standalone draft** (no ``reply_to_message_id`` / ``reply_kind``):
          ``POST /me/messages`` with the message payload — same path
          the composer has always used. ``thread_id`` / ``in_reply_to`` /
          ``references`` are accepted for signature symmetry with Gmail
          but ignored on the wire (Outlook needs ``createReply`` to
          fix ``conversationId``).

        - **Reply / Reply All / Forward draft** (``reply_to_message_id``
          and ``reply_kind`` both set): routes through
          :py:meth:`_create_draft_via_reply`, which calls
          ``POST /me/messages/{id}/createReply`` /
          ``createReplyAll`` / ``createForward`` with the message body
          in the JSON payload — a single round trip per R-09. The
          provider stitches ``conversationId`` server-side.

        CRITICAL: every Graph call here sends
        ``Prefer: IdType="ImmutableId"`` so the returned id stays stable
        across state transitions (e.g. when the draft is later sent).
        Without this header, Outlook may return a mutable ID that
        changes on send and break any future ``GET`` / ``PATCH`` /
        ``DELETE`` keyed by ``provider_draft_id``.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook create_draft requires authentication.")

        del thread_id, in_reply_to, references, original_subject  # symmetry with Gmail

        payload = self._build_draft_graph_payload(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        if reply_to_message_id and reply_kind in ("reply", "reply_all", "forward"):
            return self._create_draft_via_reply(
                reply_to_message_id=reply_to_message_id,
                kind=reply_kind,
                message_payload=payload,
                to_recipients=to_recipients,
                cc_recipients=cc_recipients,
                bcc_recipients=bcc_recipients,
                subject=subject,
                body=body,
            )

        try:
            response = self._graph_request(
                "POST",
                f"{GRAPH_BASE_URL}/me/messages",
                body=payload,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected create_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = str(response.get("id", ""))
        created_at = _parse_graph_datetime(response.get("createdDateTime"))
        updated_at = _parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _create_draft_via_reply(
        self,
        *,
        reply_to_message_id: str,
        kind: str,
        message_payload: dict[str, Any],
        to_recipients: list[str],
        cc_recipients: list[str],
        bcc_recipients: list[str],
        subject: str,
        body: str,
    ) -> DraftMetadata:
        """``createReply`` / ``createReplyAll`` / ``createForward`` with body JSON (R-09).

        Single round trip — the message payload (subject, body,
        recipients) is sent in the same call, so we don't need a
        follow-up PATCH. Graph fixes ``conversationId`` server-side so
        the draft and the eventual sent message belong to the original
        thread.

        Pre-validates ``toRecipients`` for ``reply`` / ``reply_all``:
        Graph's documented XOR constraint requires at least one
        ``toRecipients`` (either root or under ``message``); skipping
        it would produce a guaranteed 400 we want to avoid paying
        quota for. ``forward`` is allowed with empty recipients
        because the composer fills them later, and Graph allows the
        forward draft to land in Drafts even if the user has yet to
        type a recipient.
        """
        endpoint_map = {
            "reply": "createReply",
            "reply_all": "createReplyAll",
            "forward": "createForward",
        }
        endpoint = endpoint_map.get(kind)
        if endpoint is None:
            raise EmailExternalAPIError(
                f"Outlook _create_draft_via_reply called with invalid kind '{kind}'."
            )
        recipients = message_payload.get("toRecipients") or []
        if kind in ("reply", "reply_all") and not recipients:
            # Pre-check: the XOR constraint of the createReply body schema
            # rejects a payload with no toRecipients at all. Surfacing
            # locally produces a deterministic error instead of an
            # opaque 400.
            raise EmailRecipientsMissingError(
                f"Outlook {endpoint} requires at least one recipient."
            )

        encoded_id = urllib.parse.quote(reply_to_message_id, safe="")
        url = f"{GRAPH_BASE_URL}/me/messages/{encoded_id}/{endpoint}"

        # Body shape (R-09 / §15.2): ONLY ``message`` — no ``comment``,
        # no ``toRecipients`` at the root. Sending either alongside
        # ``message.body`` / ``message.toRecipients`` produces a 400
        # by Graph's XOR constraint.
        request_body = {"message": message_payload}

        try:
            response = self._graph_request(
                "POST", url, body=request_body,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected {endpoint} error ({type(exc).__name__}): {exc}"
            ) from exc

        provider_draft_id = str(response.get("id", ""))
        created_at = _parse_graph_datetime(response.get("createdDateTime"))
        updated_at = _parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
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
        Update an existing Outlook draft via PATCH /me/messages/{id}.

        The ``Prefer: IdType="ImmutableId"`` header is repeated on every
        subsequent call because the stored ``provider_draft_id`` is an
        Immutable ID (created with that same header). Without the header
        Graph may interpret the path parameter as a mutable ID and fail
        the lookup.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook update_draft requires authentication.")

        payload = self._build_draft_graph_payload(
            to_recipients, cc_recipients, bcc_recipients, subject, body,
        )

        try:
            response = self._graph_request(
                "PATCH",
                f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(provider_draft_id, safe='')}",
                body=payload,
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected update_draft error ({type(exc).__name__}): {exc}"
            ) from exc

        returned_id = str(response.get("id") or provider_draft_id)
        created_at = _parse_graph_datetime(response.get("createdDateTime"))
        updated_at = _parse_graph_datetime(response.get("lastModifiedDateTime"))

        return DraftMetadata(
            provider_draft_id=returned_id,
            to_recipients=list(to_recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            created_at=created_at,
            updated_at=updated_at,
        )

    def delete_draft(self, provider_draft_id: str) -> None:
        """Delete a draft in Outlook via DELETE /me/messages/{id}.

        Uses Prefer: IdType="ImmutableId" because the provider_draft_id
        stored locally was obtained with that header during create_draft.
        Without it, Graph would interpret the ID as a mutable folder-scoped
        ID and the request would fail.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook delete_draft requires authentication.")

        try:
            self._graph_request(
                "DELETE",
                f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(provider_draft_id, safe='')}",
                extra_headers=_PREFER_IMMUTABLE_HEADERS,
            )
        except EmailExternalAPIError:
            raise
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Outlook unexpected delete_draft error ({type(exc).__name__}): {exc}"
            ) from exc

    def fetch_drafts(self) -> list[DraftMetadata]:
        """Fetch the most recent Outlook drafts (capped at _DRAFTS_MAX_TOTAL).

        Unlike Gmail, a single paginated endpoint returns the complete
        Message object per draft (subject, body, recipients, timestamps)
        so there is no need for a second round of per-draft get calls.
        The cost of parallelism doesn't apply here because OData pagination
        is sequential by design (each page yields the URL of the next one).

        Uses $orderby=lastModifiedDateTime desc so the most recently edited
        drafts come first — with a cap of 500, this means the caller always
        receives the 500 most recently modified drafts (Graph accepts
        $top<=1000, so a single page covers the cap).

        Each page fetch is retried up to _DRAFTS_MAX_RETRIES times on
        transient EmailExternalAPIError before giving up.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_drafts requires authentication.")

        select_fields = (
            "id,subject,body,toRecipients,ccRecipients,bccRecipients,"
            "from,createdDateTime,lastModifiedDateTime"
        )
        url: str | None = (
            f"{GRAPH_BASE_URL}/me/mailFolders/drafts/messages"
            f"?$select={select_fields}"
            f"&$top={_DRAFTS_PAGE_SIZE}"
            f"&$orderby=lastModifiedDateTime%20desc"
        )

        drafts: list[DraftMetadata] = []
        while url and len(drafts) < _DRAFTS_MAX_TOTAL:
            response = self._fetch_drafts_page_with_retries(url)
            for msg in response.get("value") or []:
                try:
                    drafts.append(self._parse_outlook_draft(msg))
                except Exception as exc:
                    logger.warning(
                        "Outlook fetch_drafts: skipping unparseable draft %s: %s",
                        msg.get("id", "?"), exc,
                    )
                if len(drafts) >= _DRAFTS_MAX_TOTAL:
                    break
            url = response.get("@odata.nextLink")
        return drafts

    def _fetch_drafts_page_with_retries(self, url: str) -> dict[str, Any]:
        """Fetch one drafts page, retrying up to _DRAFTS_MAX_RETRIES times.

        Uses Prefer: IdType="ImmutableId" so the provider_draft_id is stable
        across future folder moves (consistent with create_draft).
        """
        last_exc: Exception | None = None
        for attempt in range(_DRAFTS_MAX_RETRIES + 1):
            try:
                return self._graph_request(
                    "GET", url,
                    extra_headers=_PREFER_IMMUTABLE_HEADERS,
                )
            except EmailExternalAPIError as exc:
                last_exc = exc
                if attempt < _DRAFTS_MAX_RETRIES:
                    logger.warning(
                        "Outlook drafts page fetch failed (attempt %d/%d), retrying: %s",
                        attempt + 1, _DRAFTS_MAX_RETRIES + 1, exc,
                    )
                    time.sleep(_DRAFTS_RETRY_DELAY)
                    continue
                raise
        # Unreachable in practice: the loop either returns or raises on the
        # final attempt. Guard defensively to tolerate `python -O` (which
        # strips asserts) and any unforeseen loop-exit path.
        if last_exc is not None:
            raise last_exc
        raise EmailExternalAPIError(
            "Outlook: unexpected exit from drafts retry loop without error."
        )

    def _parse_outlook_draft(self, msg: dict[str, Any]) -> DraftMetadata:
        """Convert a Graph Message JSON into DraftMetadata.

        Extracts address fields from the ``emailAddress.address`` sub-keys
        and parses ``createdDateTime`` / ``lastModifiedDateTime`` via the
        shared :py:func:`_parse_graph_datetime` helper.

        Body handling depends on ``contentType``:

        - ``HTML`` — Graph returns the content wrapped in a full
          ``<html><head><meta …us-ascii></head><body>…</body></html>``
          document (and NOT byte-for-byte the HTML we sent). It is
          flattened to the ``<body>`` fragment via
          :py:func:`flatten_html_document` so the composer is seeded with
          clean HTML.
        - anything else (legacy ``Text``) — converted to HTML via
          :py:func:`plain_text_to_html` so the persisted body is always
          HTML, matching the migration + the Gmail parse path.
        """
        provider_draft_id = str(msg.get("id") or "")
        subject = msg.get("subject") or ""
        body_section = msg.get("body") or {}
        raw_content = body_section.get("content") or ""
        content_type = (body_section.get("contentType") or "").lower()
        if content_type == "html":
            body_text = flatten_html_document(raw_content)
        else:
            body_text = plain_text_to_html(raw_content)

        def _addrs(key: str) -> list[str]:
            out: list[str] = []
            for recipient in msg.get(key) or []:
                address = ((recipient or {}).get("emailAddress") or {}).get("address")
                if address:
                    out.append(str(address))
            return out

        return DraftMetadata(
            provider_draft_id=provider_draft_id,
            to_recipients=_addrs("toRecipients"),
            cc_recipients=_addrs("ccRecipients"),
            bcc_recipients=_addrs("bccRecipients"),
            subject=subject,
            body=body_text,
            created_at=_parse_graph_datetime(msg.get("createdDateTime")),
            updated_at=_parse_graph_datetime(msg.get("lastModifiedDateTime")),
        )
