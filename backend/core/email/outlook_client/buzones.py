"""Estado y movimientos de buzon en Outlook: leido, favoritos (flag), papelera, spam y archivo."""

from __future__ import annotations

import logging
import urllib.parse

from ..email_client import SpamMoveResult
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from .transporte import GRAPH_BASE_URL

logger = logging.getLogger(__name__)


_BOX_TO_FOLDER: dict[str, str] = {
    "ALL_MAIL": "inbox",
    "SENT": "sentitems",
    "SPAM": "junkemail",
    "ARCHIVE": "archive",
}


class OutlookBuzonesMixin:
    """Metodos de estado/movimiento de buzon de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def delete_messages(self, message_ids: list[str]) -> list[str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook delete_messages requires authentication.")
        if not message_ids:
            return []
        # No-op: permanent deletion is not performed at the provider.
        # The service layer marks these as DELETED locally; the provider
        # retains the messages in Trash until its own retention policy
        # purges them.  See core_guide.md § Trash Management Operations.
        return list(message_ids)

    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook restore_from_trash requires authentication.")
        if not items:
            return {}
        results: dict[str, str] = {}
        for msg_id, dest_box in items.items():
            folder = _BOX_TO_FOLDER.get(dest_box, "inbox") if dest_box else "inbox"
            try:
                response = self._graph_request(
                    "POST",
                    f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": folder},
                )
                results[msg_id] = response.get("id", msg_id)
            except EmailExternalAPIError:
                logger.warning("Outlook restore_from_trash: failed to restore message %s", msg_id)
        return results

    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook move_to_trash requires authentication.")
        if not message_ids:
            return {}
        results: dict[str, str] = {}
        for msg_id in message_ids:
            try:
                response = self._graph_request(
                    "POST", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": "deleteditems"},
                )
                results[msg_id] = response.get("id", msg_id)
            except EmailExternalAPIError:
                logger.warning("Outlook move_to_trash: failed to trash message %s", msg_id)
        return results

    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        """Mark messages as read/unread via Microsoft Graph API. Returns IDs successfully updated."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook update_read_status requires authentication.")
        if not message_ids:
            return []
        updated: list[str] = []
        for msg_id in message_ids:
            try:
                self._graph_request("PATCH", f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}", body={"isRead": is_read})
                updated.append(msg_id)
            except EmailExternalAPIError as exc:
                # Best-effort: a single bad id (deleted server-side, etc.)
                # must not abort the whole batch. Log so silent failures are
                # observable, mirroring the behaviour of ``move_to_trash`` /
                # ``restore_from_trash``.
                logger.warning(
                    "Outlook update_read_status skipped message %s: %s", msg_id, exc,
                )
        return updated

    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        """Toggle ``flag.flagStatus`` for a single Outlook message.

        Uses the Immutable-ID Prefer header (every message-touching
        Graph call must repeat it, see core_guide.md) and retries
        transient throttling/5xx responses honouring ``Retry-After`` (3
        attempts) via :py:meth:`_graph_request_json_with_retries`. The
        toggle is idempotent at Graph: re-setting the same value is a
        no-op, so a retry after a timeout is safe.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook set_favorite requires authentication.")
        if not provider_message_id:
            return
        flag_status = "flagged" if is_favorite else "notFlagged"
        url = (
            f"{GRAPH_BASE_URL}/me/messages/"
            f"{urllib.parse.quote(provider_message_id, safe='')}"
        )
        self._graph_request_json_with_retries(
            "PATCH",
            url,
            body={"flag": {"flagStatus": flag_status}},
            operation="set_favorite",
        )

    def list_favorite_ids(self) -> list[str]:
        """List ids of every flagged Outlook message in the mailbox.

        Filters with ``$filter=flag/flagStatus eq 'flagged'`` and pages
        through ``@odata.nextLink`` until the result set is exhausted.
        ImmutableId is preferred so the returned ids stay stable across
        future moves (and match the ids already in ``email_metadata``).
        Each page (the initial request and every ``nextLink``) retries
        transient errors honouring ``Retry-After`` — a throttled page is
        retried, not fatal to the reconciliation.
        """
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook list_favorite_ids requires authentication.")
        url = (
            f"{GRAPH_BASE_URL}/me/messages"
            "?$filter=flag/flagStatus%20eq%20'flagged'"
            "&$select=id"
            "&$top=100"
        )
        ids: list[str] = []
        while url:
            response = self._graph_request_json_with_retries(
                "GET", url, operation="list_favorite_ids",
            )
            for msg in response.get("value", []) or []:
                msg_id = str(msg.get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)
            url = response.get("@odata.nextLink")
        return ids

    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Move messages to spam via Microsoft Graph API. Returns results for successfully moved messages."""
        return self._move_messages(message_ids, "junkemail", "move_to_spam")

    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Restore messages from spam via Microsoft Graph API. Returns results for successfully restored messages."""
        return self._move_messages(message_ids, "inbox", "restore_from_spam")

    def move_to_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Archive messages by moving them to the well-known 'archive' folder. The id changes (default ids + old→new rewrite). Returns results for successfully archived messages."""
        return self._move_messages(message_ids, "archive", "move_to_archive")

    def restore_from_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Unarchive messages by moving them back to the inbox. The id changes (default ids + old→new rewrite). Returns results for successfully restored messages."""
        return self._move_messages(message_ids, "inbox", "restore_from_archive")

    def _move_messages(
        self,
        message_ids: list[str],
        destination_id: str,
        operation: str,
    ) -> list[SpamMoveResult]:
        """Move messages to a folder via the Graph /move endpoint. Returns results for successfully moved messages."""
        if self._access_token is None:
            raise EmailNotAuthenticatedError(f"Outlook {operation} requires authentication.")
        if not message_ids:
            return []
        results: list[SpamMoveResult] = []
        for msg_id in message_ids:
            try:
                response = self._graph_request(
                    "POST",
                    f"{GRAPH_BASE_URL}/me/messages/{urllib.parse.quote(msg_id, safe='')}/move",
                    body={"destinationId": destination_id},
                )
                new_id = response.get("id", msg_id)
                results.append(SpamMoveResult(old_id=msg_id, new_id=new_id))
            except EmailExternalAPIError as exc:
                logger.warning(
                    "Outlook spam-move failed for message %s: %s", msg_id, exc
                )
        return results
