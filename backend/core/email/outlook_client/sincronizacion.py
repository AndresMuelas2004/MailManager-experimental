"""Sincronizacion de metadatos Outlook: bootstrap, deltas por carpeta y cursores."""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

from ..email_client import EmailMetadata, LabelUpdate, SyncResult
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from ..helpers import dedupe_metadata_by_message_id
from .contenido import OutlookContenidoMixin
from .transporte import GRAPH_BASE_URL, _parse_graph_datetime

logger = logging.getLogger(__name__)


_DELTA_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,receivedDateTime,isRead"
)
_DELTA_PAGE_SIZE = 100


_BOOTSTRAP_SELECT_FIELDS = (
    "id,conversationId,from,toRecipients,subject,"
    "receivedDateTime,isRead,parentFolderId"
)


_DELTA_FOLDERS = ("inbox", "sentitems", "drafts", "deleteditems", "junkemail", "archive")


_FOLDER_TO_BOX: dict[str, str] = {
    "deleteditems": "TRASH",
    "junkemail": "SPAM",
    "sentitems": "SENT",
    "archive": "ARCHIVE",
}


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

        received_at = _parse_graph_datetime(msg.get("receivedDateTime", ""))

        return EmailMetadata(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("conversationId") or "",
            from_email=email_address.get("address") or "",
            from_name=email_address.get("name") or "",
            subject=msg.get("subject") or "",
            received_at=received_at,
            is_read=msg.get("isRead", False),
            box=box,
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
                        label_updates.append(LabelUpdate(
                            provider_message_id=msg["id"],
                            is_read=msg.get("isRead", False),
                            box=box,
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
        """
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

    def _bootstrap_email_metadata(self, max_total: int) -> SyncResult:
        """Path 1: Fetch most recent messages across all folders, then init delta cursors."""
        # Step 1: Discover special folder IDs for box classification.
        folder_id_to_box = self._resolve_special_folder_ids()

        # Step 2: Fetch the most recent messages across all folders.
        upserts = self._fetch_recent_messages(max_total, folder_id_to_box)

        # Step 3: Initialize per-folder delta cursors for future incremental syncs.
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
                    "Outlook bootstrap: delta init for '%s' failed, skipping.",
                    folder,
                )

        return SyncResult(
            upserts=dedupe_metadata_by_message_id(upserts),
            new_cursor=self._encode_folder_cursors(folder_cursors),
            is_full_sync=True,
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
