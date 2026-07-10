"""Estado y movimientos de buzon en Gmail: leido, favoritos, papelera, spam y archivo."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from googleapiclient.errors import HttpError

from ..email_client import SpamMoveResult
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from ..helpers import http_error_detail
from ._comunes import _BATCH_MAX_RETRIES, _BATCH_RETRY_DELAY, _BATCH_SIZE, _is_retryable

logger = logging.getLogger(__name__)


class GmailBuzonesMixin:
    """Metodos de estado/movimiento de buzon de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    def delete_messages(self, message_ids: list[str]) -> list[str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail delete_messages requires authentication.")
        if not message_ids:
            return []
        # No-op: gmail.modify scope cannot call messages.delete (requires
        # restricted mail.google.com scope). Return all IDs as "succeeded" so
        # the service layer marks them DELETED locally. Gmail auto-cleans
        # trash after 30 days; reconciliation removes stale DELETED rows.
        return list(message_ids)

    def _execute_batch_modify(
        self,
        message_ids: list[str],
        request_builder: Callable[[str], Any],
        operation_name: str,
    ) -> list[str]:
        """Execute a batch modify operation (trash, untrash) in chunks.
        Returns the list of message IDs that succeeded."""
        succeeded: list[str] = []

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            failed: list[str] = []

            def _callback(
                request_id: str, response: Any, exception: Any,
                _s: list[str] = succeeded,
                _f: list[str] = failed,
            ) -> None:
                if exception is not None:
                    _f.append(request_id)
                    return
                _s.append(request_id)

            try:
                batch = self.service.new_batch_http_request(callback=_callback)
                for msg_id in chunk:
                    batch.add(request_builder(msg_id), request_id=msg_id)
                batch.execute()
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail {operation_name} batch failed (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected {operation_name} error ({type(exc).__name__}): {exc}"
                ) from exc

            if failed:
                logger.warning(
                    "Gmail %s: %d/%d messages failed in chunk: %s",
                    operation_name, len(failed), len(chunk), failed[:10],
                )

        return succeeded

    _BOX_TO_GMAIL_LABELS: dict[str, list[str]] = {
        "ALL_MAIL": ["INBOX"],
        "SPAM": ["SPAM"],
    }

    def restore_from_trash(self, items: dict[str, str | None]) -> dict[str, str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_trash requires authentication.")
        if not items:
            return {}

        known = {mid: dest for mid, dest in items.items() if dest is not None}
        unknown = [mid for mid, dest in items.items() if dest is None]

        result: dict[str, str] = {}

        # Known destination: remove TRASH + add destination labels
        if known:
            succeeded = self._execute_batch_modify(
                list(known.keys()),
                lambda mid: self.service.users().messages().modify(
                    userId="me", id=mid,
                    body={
                        "removeLabelIds": ["TRASH"],
                        "addLabelIds": self._BOX_TO_GMAIL_LABELS.get(known[mid], []),
                    },
                ),
                "restore_from_trash",
            )
            result.update({mid: mid for mid in succeeded})

        # Unknown destination: untrash (restores original label state)
        if unknown:
            succeeded = self._execute_batch_modify(
                unknown,
                lambda mid: self.service.users().messages().untrash(userId="me", id=mid),
                "restore_from_trash_untrash",
            )
            result.update({mid: mid for mid in succeeded})

        return result

    def move_to_trash(self, message_ids: list[str]) -> dict[str, str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_trash requires authentication.")
        if not message_ids:
            return {}
        succeeded = self._execute_batch_modify(
            message_ids,
            lambda mid: self.service.users().messages().trash(userId="me", id=mid),
            "move_to_trash",
        )
        # Gmail ID doesn't change on trash
        return {mid: mid for mid in succeeded}

    def update_read_status(self, message_ids: list[str], is_read: bool) -> list[str]:
        """Mark messages as read/unread via Gmail label modification. Returns IDs successfully updated."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail update_read_status requires authentication.")
        if not message_ids:
            return []
        if is_read:
            return self._batch_modify_labels(message_ids, remove_labels=["UNREAD"])
        else:
            return self._batch_modify_labels(message_ids, add_labels=["UNREAD"])


    def set_favorite(self, provider_message_id: str, is_favorite: bool) -> None:
        """Toggle the STARRED label on a single Gmail message.

        Reuses :py:meth:`_batch_modify_labels` with a one-element list
        rather than calling ``users.messages.modify`` directly — the
        batch helper already centralises the retry policy, the
        per-chunk error classification, and the no-op behaviour for
        already-applied / already-removed labels (relevant for the
        idempotent toggle semantics of the favourites endpoint).
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail set_favorite requires authentication.")
        if not provider_message_id:
            return
        labels = ["STARRED"]
        if is_favorite:
            updated = self._batch_modify_labels([provider_message_id], add_labels=labels)
        else:
            updated = self._batch_modify_labels([provider_message_id], remove_labels=labels)
        if not updated:
            raise EmailExternalAPIError(
                f"Gmail set_favorite did not affect message {provider_message_id}."
            )

    def list_favorite_ids(self) -> list[str]:
        """List message ids labelled STARRED via ``users.messages.list``.

        Each page (the initial request and every ``nextPageToken``) is
        retried on transient errors with the same manual loop the rest of
        the Gmail client uses (``_BATCH_MAX_RETRIES + 1`` = 5 attempts,
        fixed ``_BATCH_RETRY_DELAY`` = 1s between tries, classified by
        :py:func:`_is_retryable`). Google does not guarantee a
        ``Retry-After`` header, so the backoff is fixed (mirrors
        ``_batch_modify_labels`` / ``_execute_batch_get``). A transient
        hiccup on one page therefore no longer aborts the whole
        reconciliation. ``self._sleep`` keeps the waits instant in tests.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail list_favorite_ids requires authentication.")
        ids: list[str] = []
        page_token: str | None = None
        while True:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "labelIds": ["STARRED"],
                "maxResults": 500,
                "includeSpamTrash": True,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            response = self._list_favorites_page_with_retries(list_kwargs)
            for msg in response.get("messages", []) or []:
                msg_id = str(msg.get("id") or "").strip()
                if msg_id:
                    ids.append(msg_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                return ids

    def _list_favorites_page_with_retries(
        self, list_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch one STARRED-listing page, retrying transient failures.

        Mirrors the manual retry loop of ``_batch_modify_labels`` /
        ``_execute_batch_get``: up to ``_BATCH_MAX_RETRIES + 1`` attempts,
        a fixed ``_BATCH_RETRY_DELAY`` wait between them, and
        :py:func:`_is_retryable` deciding whether an exception is worth
        retrying. Non-retryable errors (404/400/403/410) propagate
        immediately as :py:class:`EmailExternalAPIError`; a still-failing
        transient after the last attempt does too.
        """
        last_exc: Exception | None = None
        for attempt in range(_BATCH_MAX_RETRIES + 1):
            try:
                return self.service.users().messages().list(**list_kwargs).execute()
            except HttpError as exc:
                if not _is_retryable(exc):
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail failed to list STARRED messages (HTTP {status}: {reason})."
                    ) from exc
                last_exc = exc
            except Exception as exc:
                # Network noise (timeouts, resets) is retryable; a genuinely
                # unexpected error is re-raised on the final attempt below.
                last_exc = exc
            if attempt < _BATCH_MAX_RETRIES:
                self._sleep(_BATCH_RETRY_DELAY)
        if isinstance(last_exc, HttpError):
            status, reason = http_error_detail(last_exc)
            raise EmailExternalAPIError(
                f"Gmail failed to list STARRED messages after "
                f"{_BATCH_MAX_RETRIES + 1} attempts (HTTP {status}: {reason})."
            ) from last_exc
        raise EmailExternalAPIError(
            f"Gmail failed to list STARRED messages after "
            f"{_BATCH_MAX_RETRIES + 1} attempts "
            f"({type(last_exc).__name__}: {last_exc})."
        ) from last_exc


    def move_to_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Move messages to spam via Gmail label modification. Returns results for successfully moved messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_spam requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, add_labels=["SPAM"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    def restore_from_spam(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Restore messages from spam via Gmail label modification. Returns results for successfully restored messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_spam requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(
            message_ids, remove_labels=["SPAM"], add_labels=["INBOX"],
        )
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]


    def move_to_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Archive messages via Gmail label modification (remove INBOX). The id does not change. Returns results for successfully archived messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail move_to_archive requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, remove_labels=["INBOX"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]

    def restore_from_archive(self, message_ids: list[str]) -> list[SpamMoveResult]:
        """Unarchive messages via Gmail label modification (add INBOX). The id does not change. Returns results for successfully restored messages."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail restore_from_archive requires authentication.")
        if not message_ids:
            return []
        updated_ids = self._batch_modify_labels(message_ids, add_labels=["INBOX"])
        return [SpamMoveResult(old_id=mid, new_id=mid) for mid in updated_ids]


    def _batch_modify_labels(
        self,
        message_ids: list[str],
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> list[str]:
        """Batch-modify labels on messages. Returns list of successfully modified IDs."""
        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        all_updated: list[str] = []

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            pending_ids = list(chunk)

            for attempt in range(_BATCH_MAX_RETRIES + 1):
                chunk_updated: list[str] = []
                failed_in_attempt: list[str] = []

                def _callback(
                    request_id: str, response: Any, exception: Any,
                    _u: list[str] = chunk_updated,
                    _f: list[str] = failed_in_attempt,
                ) -> None:
                    if exception is not None:
                        if _is_retryable(exception):
                            _f.append(request_id)
                        # Non-retryable (404, 400, etc.) → silently skip
                        return
                    _u.append(request_id)

                try:
                    batch = self.service.new_batch_http_request(callback=_callback)
                    for msg_id in pending_ids:
                        batch.add(
                            self.service.users().messages().modify(
                                userId="me", id=msg_id, body=body,
                            ),
                            request_id=msg_id,
                        )
                    batch.execute()
                except HttpError as exc:
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail batch label modify failed (HTTP {status}: {reason})."
                    ) from exc
                except Exception as exc:
                    raise EmailExternalAPIError(
                        f"Gmail unexpected batch label modify error ({type(exc).__name__}): {exc}"
                    ) from exc

                all_updated.extend(chunk_updated)

                if not failed_in_attempt:
                    break

                if attempt < _BATCH_MAX_RETRIES:
                    logger.warning(
                        "Gmail batch modify: %d/%d messages failed (attempt %d/%d), retrying",
                        len(failed_in_attempt), len(pending_ids),
                        attempt + 1, _BATCH_MAX_RETRIES + 1,
                    )
                    time.sleep(_BATCH_RETRY_DELAY)
                    pending_ids = failed_in_attempt
                else:
                    logger.warning(
                        "Gmail batch modify: %d messages lost after %d attempts: %s",
                        len(failed_in_attempt), _BATCH_MAX_RETRIES + 1,
                        failed_in_attempt[:10],
                    )

        return all_updated
