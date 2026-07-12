"""Sincronizacion de metadatos Gmail: bootstrap, incremental (History API) y lotes batch."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

import google_auth_httplib2
import httplib2
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..email_client import BackfillPage, EmailMetadata, LabelUpdate, SyncResult
from ..errors import EmailExternalAPIError, EmailNotAuthenticatedError
from ..helpers import dedupe_metadata_by_message_id, http_error_detail
from ._comunes import (
    _BATCH_MAX_RETRIES,
    _BATCH_RETRY_DELAY,
    _BATCH_SIZE,
    _PARALLEL_MAX_WORKERS,
    _first_recipient_from_to_header,
    _is_retryable,
)

logger = logging.getLogger(__name__)


_INCREMENTAL_EVENT_THRESHOLD = 100


def _log_skipped_messages(
    operation: str, skipped_ids: list[str], message_ids: list[str],
) -> None:
    if skipped_ids:
        logger.warning(
            "Gmail %s: %d/%d messages could not be fetched: %s",
            operation, len(skipped_ids), len(message_ids), skipped_ids[:10],
        )


# Module-level helpers for the attachments flow.
# Kept at module level so multiple methods (and potentially other Gmail-
# specific callers) reuse them without bouncing through ``self``.


class GmailSincronizacionMixin:
    """Metodos de sincronizacion de metadatos de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    def fetch_email_metadata(
        self,
        sync_cursor: str | None = None,
        max_total: int = 500,
    ) -> SyncResult:
        """
        Fetch email metadata from Gmail.

        Returns a SyncResult with upserts, deletes, label_updates and new_cursor.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_email_metadata requires authentication.")

        if sync_cursor is not None:
            # ------ Path 2: Incremental sync via users.history.list ------
            try:
                return self._incremental_email_metadata(sync_cursor)
            except EmailExternalAPIError:
                pass  # Fallback to bootstrap (e.g. cursor expired / 404 / 410)

        # ------ Path 1: Bootstrap (full fetch) ------
        return self._bootstrap_email_metadata(max_total)

    def _bootstrap_email_metadata(
        self,
        max_total: int,
    ) -> SyncResult:
        """Path 1: Full bootstrap fetch of message metadata."""
        history_id = self._get_current_history_id()
        message_ids = self._list_message_ids(max_total)
        metadata_list = self.fetch_messages_metadata(message_ids)
        return SyncResult(
            upserts=dedupe_metadata_by_message_id(metadata_list),
            new_cursor=history_id,
            is_full_sync=True,
        )

    def _list_message_ids_page(
        self, page_token: str | None, page_size: int,
    ) -> tuple[list[str], str | None]:
        """List ONE page of message IDs (incl. spam/trash).

        Returns ``(ids, next_page_token)`` where ``next_page_token`` is
        ``None`` when the mailbox is exhausted. Shared by the full bootstrap
        loop and the paginated backfill wave. ``page_size`` is clamped to the
        Gmail 500-per-page maximum.
        """
        list_kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": min(page_size, 500),
            "includeSpamTrash": True,
            # Exclude drafts from the listing: they live in their own table
            # (``drafts``) and must not leak into ``email_metadata``. ``in:drafts``
            # (plural) is the functional operator for the DRAFT system label.
            # The ``"DRAFT" in labelIds`` filter in ``fetch_messages_metadata``
            # is the universal safety net (covers the incremental path too);
            # this ``q`` also stops drafts from spending ``messages.get`` quota.
            "q": "-in:drafts",
        }
        if page_token:
            list_kwargs["pageToken"] = page_token

        try:
            response = self.service.users().messages().list(**list_kwargs).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to fetch message list (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected fetch message list error ({type(exc).__name__}): {exc}"
            ) from exc

        ids: list[str] = []
        for msg in response.get("messages", []):
            msg_id = str(msg.get("id") or "").strip()
            if msg_id:
                ids.append(msg_id)
        return ids, response.get("nextPageToken")

    def _list_message_ids(self, max_total: int) -> list[str]:
        """List message IDs using pagination, including spam and trash."""
        ids: list[str] = []
        page_token: str | None = None
        while True:
            page_ids, page_token = self._list_message_ids_page(page_token, max_total)
            ids.extend(page_ids)
            if len(ids) >= max_total:
                return ids[:max_total]
            if not page_token:
                return ids

    def capture_backfill_anchor(self) -> str:
        """Gmail backfill anchor: the current ``historyId`` (before listing)."""
        if self.service is None:
            raise EmailNotAuthenticatedError(
                "Gmail capture_backfill_anchor requires authentication."
            )
        return self._get_current_history_id()

    def fetch_backfill_page(
        self, cursor: str | None, page_size: int,
    ) -> BackfillPage:
        """Fetch one backfill wave: one ``messages.list`` page + its batched
        ``messages.get`` metadata. ``cursor`` is the ``messages.list``
        pageToken (``None`` for the first page)."""
        if self.service is None:
            raise EmailNotAuthenticatedError(
                "Gmail fetch_backfill_page requires authentication."
            )
        ids, next_token = self._list_message_ids_page(cursor, min(page_size, 500))
        metadata = self.fetch_messages_metadata(ids)
        return BackfillPage(
            upserts=dedupe_metadata_by_message_id(metadata),
            next_cursor=next_token,
        )

    def _execute_single_chunk(
        self,
        chunk_ids: list[str],
        chunk_index: int,
        *,
        fmt: str,
        extra_kwargs: dict[str, Any] | None = None,
        credentials: Credentials,
        resource: str = "messages",
    ) -> tuple[dict[str, dict[str, Any]], list[str], list[str], float]:
        """Execute a single batch.execute() for one chunk of IDs.

        Each call builds its own thread-local HTTP transport and service
        because httplib2 is not thread-safe. Never raises — chunk-level
        errors mark all IDs as failed.

        The ``resource`` parameter selects between ``users().messages()``
        (default) and ``users().drafts()`` so the same parallel batch
        skeleton is reused for the drafts sync.

        Returns (successes, retryable_failed_ids, permanent_failed_ids, elapsed_seconds).
        """
        t0 = time.perf_counter()
        successes: dict[str, dict[str, Any]] = {}
        failed: list[str] = []
        permanent_failed: list[str] = []

        def _callback(
            request_id: str, response: Any, exception: Any,
            _s: dict[str, dict[str, Any]] = successes,
            _f: list[str] = failed,
            _pf: list[str] = permanent_failed,
        ) -> None:
            if exception is not None:
                if _is_retryable(exception):
                    _f.append(request_id)
                else:
                    _pf.append(request_id)
                return
            _s[request_id] = response

        try:
            thread_http = google_auth_httplib2.AuthorizedHttp(
                credentials, http=httplib2.Http(timeout=30),
            )
            thread_service = build("gmail", "v1", http=thread_http)

            batch = thread_service.new_batch_http_request(callback=_callback)
            for msg_id in chunk_ids:
                get_kwargs: dict[str, Any] = {
                    "userId": "me", "id": msg_id, "format": fmt,
                    **(extra_kwargs or {}),
                }
                if resource == "drafts":
                    request = thread_service.users().drafts().get(**get_kwargs)
                else:
                    request = thread_service.users().messages().get(**get_kwargs)
                batch.add(request, request_id=msg_id)
            batch.execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            if _is_retryable(exc):
                logger.warning(
                    "Chunk %d: batch.execute() HttpError (HTTP %s: %s) — all %d IDs marked retryable",
                    chunk_index, status, reason, len(chunk_ids),
                )
                return {}, list(chunk_ids), [], time.perf_counter() - t0
            logger.warning(
                "Chunk %d: batch.execute() HttpError (HTTP %s: %s) — all %d IDs marked permanently failed",
                chunk_index, status, reason, len(chunk_ids),
            )
            return {}, [], list(chunk_ids), time.perf_counter() - t0
        except Exception as exc:
            logger.warning(
                "Chunk %d: batch.execute() %s: %s — all %d IDs marked retryable",
                chunk_index, type(exc).__name__, exc, len(chunk_ids),
            )
            return {}, list(chunk_ids), [], time.perf_counter() - t0

        return successes, failed, permanent_failed, time.perf_counter() - t0

    def _execute_batch_get_sequential(
        self,
        message_ids: list[str],
        *,
        fmt: str,
        error_context: str,
        extra_kwargs: dict[str, Any] | None = None,
        resource: str = "messages",
    ) -> dict[str, dict[str, Any]]:
        """Fallback: sequential batch execution using self.service directly.

        ``resource`` selects between ``users().messages()`` (default) and
        ``users().drafts()`` so drafts sync can reuse the same skeleton.
        """
        all_results: dict[str, dict[str, Any]] = {}

        for chunk_start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[chunk_start:chunk_start + _BATCH_SIZE]
            chunk_results: dict[str, dict[str, Any]] = {}
            pending_ids = list(chunk)
            resolved_extra = extra_kwargs or {}

            for attempt in range(_BATCH_MAX_RETRIES + 1):
                failed_in_attempt: list[str] = []

                def _callback(
                    request_id: str, response: Any, exception: Any,
                    _r: dict[str, dict[str, Any]] = chunk_results,
                    _f: list[str] = failed_in_attempt,
                ) -> None:
                    if exception is not None:
                        if _is_retryable(exception):
                            _f.append(request_id)
                        return
                    _r[request_id] = response

                try:
                    batch = self.service.new_batch_http_request(callback=_callback)
                    for msg_id in pending_ids:
                        get_kwargs: dict[str, Any] = {
                            "userId": "me", "id": msg_id, "format": fmt,
                            **resolved_extra,
                        }
                        if resource == "drafts":
                            request = self.service.users().drafts().get(**get_kwargs)
                        else:
                            request = self.service.users().messages().get(**get_kwargs)
                        batch.add(request, request_id=msg_id)
                    batch.execute()
                except HttpError as exc:
                    status, reason = http_error_detail(exc)
                    raise EmailExternalAPIError(
                        f"Gmail {error_context} failed (HTTP {status}: {reason})."
                    ) from exc
                except Exception as exc:
                    raise EmailExternalAPIError(
                        f"Gmail unexpected {error_context} error ({type(exc).__name__}): {exc}"
                    ) from exc

                if not failed_in_attempt:
                    break

                if attempt < _BATCH_MAX_RETRIES:
                    logger.warning(
                        "Gmail batch: %d/%d messages failed (attempt %d/%d), retrying",
                        len(failed_in_attempt), len(pending_ids),
                        attempt + 1, _BATCH_MAX_RETRIES + 1,
                    )
                    time.sleep(_BATCH_RETRY_DELAY)
                    pending_ids = failed_in_attempt
                else:
                    logger.warning(
                        "Gmail batch: %d messages lost after %d attempts: %s",
                        len(failed_in_attempt), _BATCH_MAX_RETRIES + 1,
                        failed_in_attempt[:10],
                    )

            all_results.update(chunk_results)

        return all_results

    def _execute_batch_get(
        self,
        message_ids: list[str],
        *,
        fmt: str,
        error_context: str,
        extra_kwargs: dict[str, Any] | None = None,
        resource: str = "messages",
    ) -> dict[str, dict[str, Any]]:
        """Execute a batched messages.get call; returns {msg_id: response}.

        When credentials are available, chunks run in parallel via
        ThreadPoolExecutor. Falls back to sequential execution otherwise.

        ``resource`` selects between ``users().messages()`` (default) and
        ``users().drafts()`` — the drafts sync reuses this skeleton so
        the same parallel-batches-of-100 + retries logic applies.
        """
        if not message_ids:
            return {}

        if self._credentials is None:
            return self._execute_batch_get_sequential(
                message_ids, fmt=fmt, error_context=error_context,
                extra_kwargs=extra_kwargs, resource=resource,
            )

        all_results: dict[str, dict[str, Any]] = {}
        chunks: list[list[str]] = [
            message_ids[i:i + _BATCH_SIZE]
            for i in range(0, len(message_ids), _BATCH_SIZE)
        ]
        num_chunks = len(chunks)
        workers = min(_PARALLEL_MAX_WORKERS, num_chunks)

        pending_per_chunk: dict[int, list[str]] = {
            i: list(chunk) for i, chunk in enumerate(chunks)
        }
        creds = self._credentials

        logger.info(
            "Gmail parallel batch: %d chunk(s), %d worker(s), %d total IDs",
            num_chunks, workers, len(message_ids),
        )

        for attempt in range(_BATCH_MAX_RETRIES + 1):
            active_chunks = {
                i: ids for i, ids in pending_per_chunk.items() if ids
            }
            if not active_chunks:
                break

            if attempt > 0:
                logger.warning(
                    "Gmail parallel batch: retry %d/%d — %d IDs pending across %d chunk(s)",
                    attempt, _BATCH_MAX_RETRIES,
                    sum(len(ids) for ids in active_chunks.values()),
                    len(active_chunks),
                )
                time.sleep(_BATCH_RETRY_DELAY)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._execute_single_chunk,
                        ids, chunk_idx,
                        fmt=fmt,
                        extra_kwargs=extra_kwargs, credentials=creds,
                        resource=resource,
                    ): chunk_idx
                    for chunk_idx, ids in active_chunks.items()
                }
                for future in as_completed(futures):
                    chunk_idx = futures[future]
                    successes, failed, permanent_failed, _elapsed = future.result()
                    all_results.update(successes)
                    pending_per_chunk[chunk_idx] = failed
                    if permanent_failed:
                        logger.warning(
                            "Gmail parallel batch: %d permanent failures in chunk %d: %s",
                            len(permanent_failed), chunk_idx, permanent_failed[:10],
                        )

            total_pending = sum(len(ids) for ids in pending_per_chunk.values())
            if total_pending == 0:
                break

            if attempt == _BATCH_MAX_RETRIES:
                lost_ids = [
                    msg_id
                    for ids in pending_per_chunk.values()
                    for msg_id in ids
                ]
                logger.warning(
                    "Gmail parallel batch: %d messages lost after %d attempts: %s",
                    len(lost_ids), _BATCH_MAX_RETRIES + 1, lost_ids[:10],
                )

        return all_results

    def fetch_messages_metadata(self, message_ids: list[str]) -> list[EmailMetadata]:
        """Fetch metadata for message IDs using Gmail BatchHttpRequest."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail fetch_messages_metadata requires authentication.")
        if not message_ids:
            return []
        raw = self._execute_batch_get(
            message_ids,
            fmt="metadata",
            error_context="batch metadata fetch",
            extra_kwargs={"metadataHeaders": ["From", "To", "Subject"]},
        )
        results: list[EmailMetadata] = []
        skipped_ids: list[str] = []
        for msg_id in message_ids:
            msg = raw.get(msg_id)
            if msg is None:
                skipped_ids.append(msg_id)
                continue
            # Universal draft exclusion (covers the incremental path, where the
            # ``q="-in:drafts"`` listing filter does not apply): a message the
            # provider labels DRAFT belongs to the ``drafts`` table, never to
            # ``email_metadata``. Silently drop it (not a skip/error).
            if "DRAFT" in (msg.get("labelIds") or []):
                logger.debug("Gmail skipping draft message %s (excluded from email_metadata).", msg_id)
                continue
            try:
                results.append(self._parse_metadata_response(msg))
            except Exception as exc:
                logger.debug("Gmail skipped unparseable message %s: %s", msg_id, exc)
                skipped_ids.append(msg_id)
        _log_skipped_messages("metadata sync", skipped_ids, message_ids)
        return results

    @staticmethod
    def _resolve_labels(label_ids: list[str]) -> tuple[bool, str]:
        """Map Gmail label IDs to (is_read, box)."""
        labels = set(label_ids)
        is_read = "UNREAD" not in labels
        if "TRASH" in labels:
            box = "TRASH"
        elif "SPAM" in labels:
            box = "SPAM"
        elif "SENT" in labels:
            box = "SENT"
        elif "INBOX" in labels:
            box = "ALL_MAIL"
        else:
            # Received message with INBOX removed (and not SPAM/TRASH/SENT):
            # this is what "archived in Gmail" means — the message lives in
            # All Mail without the INBOX label.
            box = "ARCHIVE"
        return is_read, box

    @staticmethod
    def _parse_metadata_response(msg: dict[str, Any]) -> EmailMetadata:
        """Parse a Gmail message response (format=metadata) into EmailMetadata."""
        headers = {}
        for h in (msg.get("payload") or {}).get("headers", []):
            name = h.get("name")
            if name:
                headers[name] = h.get("value", "")

        from_header = headers.get("From", "")
        from_name, from_email = parseaddr(from_header)

        to_name, to_email = _first_recipient_from_to_header(headers.get("To", ""))

        label_ids = msg.get("labelIds") or []
        is_read, box = GmailSincronizacionMixin._resolve_labels(label_ids)
        is_favorite = "STARRED" in label_ids

        internal_date = msg.get("internalDate")
        if internal_date:
            try:
                received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                received_at = datetime.now(timezone.utc)
        else:
            received_at = datetime.now(timezone.utc)

        return EmailMetadata(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("threadId", ""),
            from_email=from_email or "",
            from_name=from_name or "",
            subject=headers.get("Subject", ""),
            received_at=received_at,
            is_read=is_read,
            box=box,
            is_favorite=is_favorite,
            to_email=to_email,
            to_name=to_name,
        )

    def _fetch_sender_email(self) -> str:
        """Best-effort fetch of the authenticated user's email address (cached)."""
        if self._sender_email is not None:
            return self._sender_email
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            self._sender_email = profile.get("emailAddress", "")
        except Exception as exc:
            logger.debug("Gmail failed to fetch sender email: %s", exc)
            self._sender_email = ""
        return self._sender_email

    def _get_current_history_id(self) -> str:
        """Retrieve the current historyId from the user's Gmail profile."""
        try:
            profile = self.service.users().getProfile(userId="me").execute()
            return str(profile.get("historyId", ""))
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise EmailExternalAPIError(
                f"Gmail failed to fetch profile for historyId (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise EmailExternalAPIError(
                f"Gmail unexpected getProfile error ({type(exc).__name__}): {exc}"
            ) from exc

    def _incremental_email_metadata(
        self, sync_cursor: str,
    ) -> SyncResult:
        """Path 2: Incremental sync via Gmail History API."""
        # Step 1 — Paginate history.list and classify events into 3 sets
        need_get_ids: set[str] = set()
        pending_delete_ids: set[str] = set()
        label_change_ids: set[str] = set()
        new_history_id = sync_cursor

        page_token: str | None = None
        while True:
            list_kwargs: dict[str, Any] = {
                "userId": "me",
                "startHistoryId": sync_cursor,
                "maxResults": 500,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token

            try:
                response = (
                    self.service.users().history().list(**list_kwargs).execute()
                )
            except HttpError as exc:
                status, reason = http_error_detail(exc)
                raise EmailExternalAPIError(
                    f"Gmail history.list failed (HTTP {status}: {reason})."
                ) from exc
            except Exception as exc:
                raise EmailExternalAPIError(
                    f"Gmail unexpected history.list error ({type(exc).__name__}): {exc}"
                ) from exc

            new_history_id = str(response.get("historyId", new_history_id))

            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg_id = str(added.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        need_get_ids.add(msg_id)

                for deleted in record.get("messagesDeleted", []):
                    msg_id = str(deleted.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        pending_delete_ids.add(msg_id)

                for label_event in record.get("labelsAdded", []):
                    msg_id = str(label_event.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        label_change_ids.add(msg_id)

                for label_event in record.get("labelsRemoved", []):
                    msg_id = str(label_event.get("message", {}).get("id") or "").strip()
                    if msg_id:
                        label_change_ids.add(msg_id)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Threshold check — abort if too many events (Step 1 is cheap, Steps 2-5 are not)
        total_event_ids = len(need_get_ids | pending_delete_ids | label_change_ids)
        if total_event_ids > _INCREMENTAL_EVENT_THRESHOLD:
            logger.info(
                "Gmail incremental: %d event IDs exceed threshold %d, falling back to bootstrap.",
                total_event_ids, _INCREMENTAL_EVENT_THRESHOLD,
            )
            raise EmailExternalAPIError(
                f"Gmail incremental sync has {total_event_ids} events, exceeding threshold."
            )

        # Step 2 — Resolve pending deletes: batch probe in groups of 100
        probe_ids = list(pending_delete_ids - need_get_ids)
        confirmed_deletes: list[str] = []
        if probe_ids:
            raw = self._execute_batch_get(
                probe_ids, fmt="minimal", error_context="delete probe",
            )
            for msg_id in probe_ids:
                if msg_id in raw:
                    need_get_ids.add(msg_id)
                else:
                    confirmed_deletes.append(msg_id)

        # Step 3 — Filter label changes: exclude messages already in get/delete
        label_only_ids = label_change_ids - need_get_ids - set(confirmed_deletes)

        # Step 4 — Batch fetch full metadata for need_get
        upserts = self.fetch_messages_metadata(list(need_get_ids)) if need_get_ids else []

        # Step 5 — Batch fetch label updates for label-only changes
        label_updates = (
            self._batch_fetch_label_updates(list(label_only_ids))
            if label_only_ids
            else []
        )

        return SyncResult(
            upserts=upserts,
            new_cursor=new_history_id,
            deletes=confirmed_deletes,
            label_updates=label_updates,
        )

    def _batch_fetch_label_updates(self, message_ids: list[str]) -> list[LabelUpdate]:
        """Fetch current labelIds for messages and build LabelUpdate objects."""
        raw = self._execute_batch_get(
            message_ids,
            fmt="minimal",
            error_context="batch label fetch",
        )
        results: list[LabelUpdate] = []
        skipped_ids: list[str] = []
        for msg_id in message_ids:
            msg = raw.get(msg_id)
            if msg is None:
                skipped_ids.append(msg_id)
                continue
            label_ids = msg.get("labelIds") or []
            is_read, box = self._resolve_labels(label_ids)
            results.append(LabelUpdate(
                provider_message_id=msg.get("id", msg_id),
                is_read=is_read,
                box=box,
                # Close the Gmail incremental favourite gap: a star/unstar on an
                # existing message arrives here (labelsAdded/Removed), so carry
                # the current STARRED state. ``format=minimal`` returns the full
                # labelIds, so Gmail ALWAYS populates a concrete bool (never None).
                is_favorite="STARRED" in label_ids,
            ))
        _log_skipped_messages("label sync", skipped_ids, message_ids)
        return results


    def verify_message_existence(self, message_ids: list[str]) -> list[str]:
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail verify_message_existence requires authentication.")
        if not message_ids:
            return []
        raw = self._execute_batch_get(
            message_ids, fmt="minimal", error_context="message existence verification",
        )
        return [msg_id for msg_id in message_ids if msg_id in raw]

