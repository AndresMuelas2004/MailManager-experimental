"""Sincronizacion de metadatos Outlook: bootstrap, deltas por carpeta y cursores."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from typing import Any

from ..email_client import BackfillPage, EmailMetadata, LabelUpdate, SyncResult
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from ..helpers import dedupe_metadata_by_message_id
from .contenido import OutlookContenidoMixin
from .transporte import GRAPH_BASE_URL, _parse_graph_datetime

logger = logging.getLogger(__name__)


# ``sentDateTime`` rides along ONLY as the ``received_at`` fallback of
# ``_parse_graph_message``: every endpoint (delta, bootstrap, conversation)
# must derive ``received_at`` from the SAME field chain, or the conversation
# id reconciliation's identity (received_at, from, subject) diverges between
# what sync stored and what the viewer fetched for date-less messages.
_DELTA_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,sentDateTime,isRead,flag"
)
_DELTA_PAGE_SIZE = 100


_BOOTSTRAP_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,"
    "receivedDateTime,sentDateTime,isRead,parentFolderId,flag,isDraft"
)


# ``drafts`` is deliberately absent: drafts sync into their own ``drafts`` table
# and must not leak into ``email_metadata``. Dropping it here means the delta
# anchor (``capture_backfill_anchor`` / ``_prime_folder_delta_cursors``) never
# primes nor walks the drafts folder. ``_incremental_email_metadata`` also skips
# a stale ``drafts`` cursor inherited from a pre-change ``sync_cursor``.
_DELTA_FOLDERS = ("inbox", "sentitems", "deleteditems", "junkemail", "archive")


_FOLDER_TO_BOX: dict[str, str] = {
    "deleteditems": "TRASH",
    "junkemail": "SPAM",
    "sentitems": "SENT",
    "archive": "ARCHIVE",
}


# Process-level cache of the special-folder id -> box map, keyed by the stable
# ``account_label`` ("{mailbox_id}__{account_id}"). ``OutlookClient`` is rebuilt
# per request (``build_manager_for_accounts`` makes fresh clients each time), so
# a per-instance cache never survives between conversation opens — only a
# module-level (process) cache does. Single uvicorn worker (MVP), same profile
# as ``api/rate_limit.py`` and the backfill worker. Graph returns a STABLE id
# for each well-known folder of a mailbox, so caching the map is safe; the TTL
# only bounds the rare case of a recreated mailbox. Values are
# ``(monotonic_ts, map)``. An INCOMPLETE resolution (any folder lookup failed,
# empty included) is deliberately NOT cached — see
# ``_resolve_special_folder_ids``.
_SPECIAL_FOLDER_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_SPECIAL_FOLDER_CACHE_LOCK = threading.Lock()
_SPECIAL_FOLDER_TTL_S = 3600.0  # 1 hour (conservative; the ids are stable anyway)


class OutlookSincronizacionMixin:
    """Metodos de sincronizacion de metadatos de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        """Fetch email metadata from Outlook using Microsoft Graph Delta Query."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_email_metadata requires authentication.")

        if sync_cursor is not None:
            try:
                return self._incremental_email_metadata(sync_cursor)
            except EmailExternalAPIError:
                pass  # Fallback to bootstrap (e.g. expired deltaLink)

        return self._bootstrap_email_metadata(max_total)

    @staticmethod
    def _encode_folder_cursors(folder_cursors: dict[str, str]) -> str:
        """Serialize per-folder deltaLinks into a JSON cursor string."""
        return json.dumps({"v": 1, "folders": folder_cursors})

    @staticmethod
    def _decode_folder_cursors(sync_cursor: str) -> dict[str, str] | None:
        """Deserialize a JSON cursor string. Returns None if invalid or legacy format."""
        try:
            data = json.loads(sync_cursor)
            if isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get("folders"), dict):
                return data["folders"]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _parse_graph_message(msg: dict[str, Any], box: str) -> EmailMetadata:
        """Parse a Microsoft Graph message resource into EmailMetadata."""
        from_obj = msg.get("from") or {}
        email_address = from_obj.get("emailAddress") or {}

        to_name, to_email = OutlookContenidoMixin._first_recipient_from_graph_recipients(
            msg.get("toRecipients"),
        )

        # Single received_at derivation for EVERY endpoint that parses a
        # message (delta, bootstrap, conversation): receivedDateTime, falling
        # back to sentDateTime when absent (Sent/date-less items), then the
        # deterministic epoch fallback inside ``_parse_graph_datetime``.
        # Keeping this chain identical across endpoints is load-bearing for
        # the conversation id reconciliation, whose identity key starts with
        # ``received_at``.
        received_at = _parse_graph_datetime(
            msg.get("receivedDateTime") or msg.get("sentDateTime") or "",
        )

        return EmailMetadata(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("conversationId") or "",
            from_email=email_address.get("address") or "",
            from_name=email_address.get("name") or "",
            subject=msg.get("subject") or "",
            received_at=received_at,
            is_read=msg.get("isRead", False),
            box=box,
            is_favorite=(msg.get("flag") or {}).get("flagStatus") == "flagged",
            to_email=to_email,
            to_name=to_name,
        )

    def _fetch_folder_delta(
        self,
        folder_name: str,
        url: str,
        upserts: list[EmailMetadata],
        deletes: list[str],
        max_collect: int | None = None,
        label_updates: list[LabelUpdate] | None = None,
    ) -> str:
        """Paginate a single folder's delta query until deltaLink is obtained.

        Appends parsed messages to *upserts* and removed IDs to *deletes*.
        When *label_updates* is provided, partial delta messages (missing
        ``from``) are appended there instead of being parsed as full upserts.
        When *max_collect* is set, stops collecting messages after the limit
        but continues paginating to obtain the deltaLink.
        Returns the deltaLink.
        """
        box = _FOLDER_TO_BOX.get(folder_name, "ALL_MAIL")
        collected_enough = False

        while True:
            response = self._graph_request("GET", url)
            if not collected_enough:
                for msg in response.get("value", []):
                    if max_collect is not None and len(upserts) >= max_collect:
                        collected_enough = True
                        break
                    if msg.get("@removed"):
                        msg_id = msg.get("id", "")
                        if msg_id:
                            deletes.append(msg_id)
                            logger.debug(
                                "Outlook delta [%s] REMOVED id=%s",
                                folder_name, msg_id,
                            )
                    elif "from" not in msg and label_updates is not None:
                        # Partial delta object (only isRead/labels changed).
                        # Carry ``is_favorite`` ONLY when ``flag`` is present in
                        # the partial payload; otherwise leave it None so the
                        # COALESCE in UPDATE_LABELS_BATCH keeps the stored value
                        # (an out-of-band favourite change on Outlook normally
                        # arrives as a full upsert, not a partial label update).
                        is_favorite = (
                            (msg.get("flag") or {}).get("flagStatus") == "flagged"
                            if "flag" in msg
                            else None
                        )
                        label_updates.append(LabelUpdate(
                            provider_message_id=msg["id"],
                            is_read=msg.get("isRead", False),
                            box=box,
                            is_favorite=is_favorite,
                        ))
                        logger.debug(
                            "Outlook delta [%s] LABEL id=%s is_read=%s",
                            folder_name, msg["id"], msg.get("isRead"),
                        )
                    else:
                        try:
                            upserts.append(self._parse_graph_message(msg, box))
                            logger.debug(
                                "Outlook delta [%s] UPSERT id=%s subject=%r box=%s",
                                folder_name, msg.get("id", "?"),
                                msg.get("subject", "?"), box,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Outlook delta (%s): skipping unparseable message %s: %s",
                                folder_name, msg.get("id", "?"), exc,
                            )

            next_link = response.get("@odata.nextLink")
            delta_link = response.get("@odata.deltaLink")

            if delta_link:
                return delta_link

            if not next_link:
                logger.warning("Outlook delta (%s): response has neither nextLink nor deltaLink", folder_name)
                return ""

            url = next_link

    def _resolve_special_folder_ids(self) -> dict[str, str]:
        """Fetch Graph IDs for sentitems, deleteditems, junkemail and archive.

        Returns a mapping {folder_id: box} so that each message's
        parentFolderId can be classified into SENT, TRASH, SPAM or ARCHIVE.
        Any folder not in this mapping defaults to ALL_MAIL. The set of
        folders resolved is driven dynamically by ``_FOLDER_TO_BOX``.

        Backed by a process-level TTL cache keyed on ``account_label`` so the
        4 Graph calls run at most once per account per TTL — the hot path is
        ``fetch_conversation``, which resolves these on every conversation open
        and would otherwise pay 4 round trips each time. A fresh copy is
        returned on every call so a caller can never mutate the cached map.
        """
        account_label = self._account_label
        now = time.monotonic()
        with _SPECIAL_FOLDER_CACHE_LOCK:
            entry = _SPECIAL_FOLDER_CACHE.get(account_label)
            if entry is not None and now - entry[0] < _SPECIAL_FOLDER_TTL_S:
                return dict(entry[1])

        folder_id_to_box: dict[str, str] = {}
        for folder_name, box in _FOLDER_TO_BOX.items():
            try:
                url = f"{GRAPH_BASE_URL}/me/mailFolders/{folder_name}?$select=id"
                response = self._graph_request("GET", url)
                folder_id = response.get("id", "")
                if folder_id:
                    folder_id_to_box[folder_id] = box
            except EmailExternalAPIError:
                logger.warning(
                    "Outlook bootstrap: failed to resolve folder '%s', skipping.",
                    folder_name,
                )

        # Cache only a COMPLETE map (every folder in _FOLDER_TO_BOX resolved).
        # A partial map — one folder's lookup hit a transient throttle/5xx —
        # would misclassify that folder's messages as ALL_MAIL for a whole
        # TTL; and since the conversation lazy-sync now UPDATEs the canonical
        # rows with the box it derives here, a cached partial map would move
        # e.g. every SENT message of an opened thread into the inbox listing
        # for an hour. Skipping the write lets the next call retry cheaply
        # and self-heal (the partial result is still returned for THIS call —
        # best-effort, same as before).
        if len(folder_id_to_box) == len(_FOLDER_TO_BOX):
            with _SPECIAL_FOLDER_CACHE_LOCK:
                _SPECIAL_FOLDER_CACHE[account_label] = (now, dict(folder_id_to_box))
        return folder_id_to_box

    def _fetch_recent_messages(
        self,
        max_total: int,
        folder_id_to_box: dict[str, str],
    ) -> list[EmailMetadata]:
        """Fetch the most recent messages across all folders via GET /me/messages.

        Uses $orderby=receivedDateTime desc so the API returns messages
        sorted by date (most recent first).  The parentFolderId of each
        message is compared against *folder_id_to_box* to assign the
        correct box value; anything not matched defaults to ALL_MAIL.
        """
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            f"?$select={_BOOTSTRAP_SELECT_FIELDS}"
            f"&$orderby=receivedDateTime+desc"
            f"&$top={min(max_total, 1000)}"
        )
        upserts: list[EmailMetadata] = []

        while url and len(upserts) < max_total:
            response = self._graph_request("GET", url)
            for msg in response.get("value", []):
                if len(upserts) >= max_total:
                    break
                # Drafts belong to the ``drafts`` table, not ``email_metadata``.
                # Filtering client-side (not via $filter=isDraft eq false, which
                # collides with $orderby — see plan §0).
                if msg.get("isDraft") is True:
                    continue
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                try:
                    upserts.append(self._parse_graph_message(msg, box))
                except Exception as exc:
                    logger.warning(
                        "Outlook bootstrap: skipping unparseable message %s: %s",
                        msg.get("id", "?"), exc,
                    )
            url = response.get("@odata.nextLink")

        return upserts

    def _prime_folder_delta_cursors(self) -> dict[str, str]:
        """Prime the per-folder delta cursors, returning ``{folder: deltaLink}``.

        Paginates each folder's delta query with ``max_collect=0`` (capture
        the deltaLink without collecting any messages). Shared by the
        bootstrap (step 3) and the backfill anchor capture. A folder that
        fails is logged and omitted.
        """
        folder_cursors: dict[str, str] = {}
        for folder in _DELTA_FOLDERS:
            url = (
                f"{GRAPH_BASE_URL}/me/mailFolders/{folder}/messages/delta"
                f"?$select={_DELTA_SELECT_FIELDS}&$top={_DELTA_PAGE_SIZE}"
            )
            try:
                delta_link = self._fetch_folder_delta(
                    folder, url, [], [],
                    max_collect=0,
                )
                if delta_link:
                    folder_cursors[folder] = delta_link
            except EmailExternalAPIError:
                logger.warning(
                    "Outlook: delta init for '%s' failed, skipping.",
                    folder,
                )
        return folder_cursors

    def _folder_id_to_box_cached(self) -> dict[str, str]:
        """Resolve special-folder IDs once per client instance and cache them.

        The backfill worker keeps the client alive across all waves of an
        account, so this cache survives between waves (only a process
        restart re-resolves it). The regular bootstrap / single-message
        paths keep re-resolving per call — unchanged.
        """
        if self._backfill_folder_map is None:
            self._backfill_folder_map = self._resolve_special_folder_ids()
        return self._backfill_folder_map

    def _bootstrap_email_metadata(self, max_total: int) -> SyncResult:
        """Path 1: Fetch most recent messages across all folders, then init delta cursors."""
        # Step 1: Discover special folder IDs for box classification.
        folder_id_to_box = self._resolve_special_folder_ids()

        # Step 2: Fetch the most recent messages across all folders.
        upserts = self._fetch_recent_messages(max_total, folder_id_to_box)

        # Step 3: Initialize per-folder delta cursors for future incremental syncs.
        folder_cursors = self._prime_folder_delta_cursors()

        return SyncResult(
            upserts=dedupe_metadata_by_message_id(upserts),
            new_cursor=self._encode_folder_cursors(folder_cursors),
            is_full_sync=True,
        )

    def capture_backfill_anchor(self) -> str:
        """Outlook backfill anchor: the per-folder delta links, primed + encoded.

        Captured at the START of the backfill so mail arriving during the
        backfill is replayed by the first incremental sync.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook capture_backfill_anchor requires authentication."
            )
        return self._encode_folder_cursors(self._prime_folder_delta_cursors())

    def fetch_backfill_page(
        self, cursor: str | None, page_size: int,
    ) -> BackfillPage:
        """Fetch one backfill wave of recent messages across all folders.

        ``cursor`` is ``None`` for the first page (builds the ordered initial
        query) or an ``@odata.nextLink`` for subsequent pages. The GET is
        routed through ``_graph_request_json_with_retries`` so it honours
        ``Retry-After`` and retries transient 429/5xx — ``_graph_request``
        (used by the plain bootstrap) does neither.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError(
                "Outlook fetch_backfill_page requires authentication."
            )
        if cursor is None:
            url = (
                f"{GRAPH_BASE_URL}/me/messages"
                f"?$select={_BOOTSTRAP_SELECT_FIELDS}"
                f"&$orderby=receivedDateTime+desc"
                f"&$top={min(page_size, 1000)}"
            )
        else:
            url = cursor

        folder_id_to_box = self._folder_id_to_box_cached()
        response = self._graph_request_json_with_retries(
            "GET", url, operation="backfill page fetch",
        )

        upserts: list[EmailMetadata] = []
        for msg in response.get("value", []):
            # Drafts sync into their own table — keep them out of the backfill.
            if msg.get("isDraft") is True:
                continue
            parent_folder_id = msg.get("parentFolderId", "")
            box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
            try:
                upserts.append(self._parse_graph_message(msg, box))
            except Exception as exc:
                logger.warning(
                    "Outlook backfill: skipping unparseable message %s: %s",
                    msg.get("id", "?"), exc,
                )

        return BackfillPage(
            upserts=dedupe_metadata_by_message_id(upserts),
            next_cursor=response.get("@odata.nextLink"),
        )

    def _incremental_email_metadata(self, sync_cursor: str) -> SyncResult:
        """Path 2: Incremental sync via per-folder stored deltaLinks."""
        folder_cursors = self._decode_folder_cursors(sync_cursor)
        if folder_cursors is None:
            raise EmailExternalAPIError("Outlook: invalid or legacy sync cursor, falling back to bootstrap.")

        upserts: list[EmailMetadata] = []
        deletes: list[str] = []
        label_updates: list[LabelUpdate] = []
        new_cursors: dict[str, str] = {}
        all_failed = True

        for folder, delta_link in folder_cursors.items():
            # Defensive skip: an account synced before drafts were excluded
            # still carries a ``drafts`` cursor in its stored ``sync_cursor``.
            # Drop it here (and from ``new_cursors``) so those accounts stop
            # pulling drafts into ``email_metadata`` without waiting for a
            # re-bootstrap. ``drafts`` is no longer in ``_DELTA_FOLDERS``, so
            # freshly primed cursors never carry it in the first place.
            if folder == "drafts":
                continue
            try:
                new_delta = self._fetch_folder_delta(
                    folder, delta_link, upserts, deletes,
                    label_updates=label_updates,
                )
                new_cursors[folder] = new_delta if new_delta else delta_link
                all_failed = False
            except EmailExternalAPIError:
                logger.warning("Outlook incremental: folder '%s' failed, keeping previous cursor.", folder)
                new_cursors[folder] = delta_link

        if all_failed and folder_cursors:
            raise EmailExternalAPIError("Outlook: all folder delta queries failed.")

        return SyncResult(
            upserts=dedupe_metadata_by_message_id(upserts),
            new_cursor=self._encode_folder_cursors(new_cursors),
            deletes=deletes,
            label_updates=label_updates,
        )

    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook fetch_messages_metadata requires authentication.")
        if not message_ids:
            return []
        folder_id_to_box = self._resolve_special_folder_ids()
        results: list[EmailMetadata] = []
        for msg_id in message_ids:
            url = f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}?$select={_BOOTSTRAP_SELECT_FIELDS}"
            try:
                msg = self._graph_request("GET", url)
                parent_folder_id = msg.get("parentFolderId", "")
                box = folder_id_to_box.get(parent_folder_id, "ALL_MAIL")
                results.append(self._parse_graph_message(msg, box))
            except EmailExternalAPIError:
                logger.warning("Outlook fetch_messages_metadata: failed to fetch %s", msg_id)
        return results

    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook verify_message_existence requires authentication.")
        if not message_ids:
            return []
        existing: list[str] = []
        for msg_id in message_ids:
            url = f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}?$select=id"
            try:
                self._graph_request("GET", url)
                existing.append(msg_id)
            except EmailExternalAPIError as exc:
                logger.warning(
                    "Outlook verify_message_existence failed for message %s: %s",
                    msg_id, exc,
                )
        return existing
