"""Etiquetas de usuario de Gmail (carpetas de MISSELA).

A MISSELA folder materialises in a Gmail account as a **user label**
(``users.labels``). The whole surface is covered by the existing ``gmail.modify``
scope — no new scope. Applying/removing a label to messages reuses
``_batch_modify_labels`` (GmailBuzonesMixin), since Gmail never rewrites a
message id on a label change (unlike Outlook's category read-modify-write).
"""

from __future__ import annotations

import logging
from typing import Any

from googleapiclient.errors import HttpError

from ..errors import EmailNotAuthenticatedError, LabelOperationError
from ..helpers import http_error_detail

logger = logging.getLogger(__name__)


class GmailEtiquetasMixin:
    """Metodos de etiquetas de usuario de :class:`~core.email.gmail_client.cliente.GmailClient`."""

    def ensure_user_label(self, name: str) -> str:
        """Return the id of the user label named *name*, creating it if absent.

        Adoption (decision 12): an existing ``type='user'`` label whose name
        equals *name* (case-insensitive) is reused; only when none matches is a
        new label created. The provider rejects creating a duplicate-named user
        label, which is exactly why the list-first step exists.
        """
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail ensure_user_label requires authentication.")
        try:
            response = self.service.users().labels().list(userId="me").execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise LabelOperationError(
                f"Gmail labels.list failed while ensuring folder label (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise LabelOperationError(
                f"Gmail unexpected labels.list error ({type(exc).__name__}): {exc}"
            ) from exc

        target = name.strip().lower()
        for label in response.get("labels", []) or []:
            if label.get("type") == "user" and str(label.get("name", "")).strip().lower() == target:
                return str(label.get("id"))

        try:
            created = self.service.users().labels().create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            ).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise LabelOperationError(
                f"Gmail labels.create failed for folder label (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise LabelOperationError(
                f"Gmail unexpected labels.create error ({type(exc).__name__}): {exc}"
            ) from exc
        return str(created.get("id"))

    def rename_user_label(self, label_id: str, new_name: str) -> None:
        """Rename a user label in place (``labels.patch``)."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail rename_user_label requires authentication.")
        try:
            self.service.users().labels().patch(
                userId="me", id=label_id, body={"name": new_name},
            ).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            raise LabelOperationError(
                f"Gmail labels.patch failed while renaming folder label (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise LabelOperationError(
                f"Gmail unexpected labels.patch error ({type(exc).__name__}): {exc}"
            ) from exc

    def delete_user_label(self, label_id: str) -> None:
        """Delete a user label — also removes it from every message (not the
        messages themselves)."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail delete_user_label requires authentication.")
        try:
            self.service.users().labels().delete(userId="me", id=label_id).execute()
        except HttpError as exc:
            status, reason = http_error_detail(exc)
            # A 404 means the label is already gone — treat as success so the
            # local delete/reflection stays idempotent.
            if status == 404:
                logger.info("Gmail delete_user_label: label %s already absent.", label_id)
                return
            raise LabelOperationError(
                f"Gmail labels.delete failed for folder label (HTTP {status}: {reason})."
            ) from exc
        except Exception as exc:
            raise LabelOperationError(
                f"Gmail unexpected labels.delete error ({type(exc).__name__}): {exc}"
            ) from exc

    def add_label_to_messages(self, label_id: str, message_ids: list[str]) -> list[str]:
        """Apply a label to messages (batched, retried). Gmail never rewrites a
        message id on a label change, so the same ids are returned."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail add_label_to_messages requires authentication.")
        if not message_ids:
            return []
        return self._batch_modify_labels(message_ids, add_labels=[label_id])

    def remove_label_from_messages(self, label_id: str, message_ids: list[str]) -> list[str]:
        """Remove a label from messages (batched, retried)."""
        if self.service is None:
            raise EmailNotAuthenticatedError("Gmail remove_label_from_messages requires authentication.")
        if not message_ids:
            return []
        # ``_batch_modify_labels`` is provided by GmailBuzonesMixin (both are
        # bases of GmailClient); it batches by 100 with retries.
        return self._batch_modify_labels(message_ids, remove_labels=[label_id])
