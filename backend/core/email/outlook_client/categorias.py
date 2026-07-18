"""Categorias de Outlook (carpetas de MISSELA).

A MISSELA folder materialises in an Outlook account as a **category**
(``message.categories`` — a list of display-name strings). Applying/removing a
category is a **read-modify-write** because a Graph ``PATCH`` REPLACES the whole
``categories`` array: read the FRESH list from the provider, add/remove the name,
PATCH the full result. Reading fresh (never reconstructing from the local
``email_folder_members`` snapshot, which only tracks MISSELA-managed folders) is
mandatory — a PATCH built from local state would silently drop the user's own
foreign categories (real data loss). The master-category list
(``/me/outlook/masterCategories``) is NOT touched: it would need the prohibited
``MailboxSettings.ReadWrite`` scope, so a category applied without a master entry
works but shows without a colour in Outlook (accepted).

Everything is covered by the existing ``Mail.ReadWrite`` scope — no new scope.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..errors import (
    CategoryOperationError,
    EmailExternalAPIError,
    EmailNotAuthenticatedError,
)
from .transporte import GRAPH_BASE_URL

logger = logging.getLogger(__name__)


class OutlookCategoriasMixin:
    """Metodos de categorias de :class:`~core.email.outlook_client.cliente.OutlookClient`."""

    def add_category_to_message(self, message_id: str, category_name: str) -> str:
        """Add *category_name* to a message (read-modify-write). Returns the
        message id (Graph does not move the message on a category change, but the
        returned id is captured for robustness)."""
        return self._apply_category(message_id, category_name, add=True)

    def remove_category_from_message(self, message_id: str, category_name: str) -> str:
        """Remove *category_name* from a message (read-modify-write)."""
        return self._apply_category(message_id, category_name, add=False)

    def _apply_category(self, message_id: str, category_name: str, *, add: bool) -> str:
        if self._access_token is None:
            raise EmailNotAuthenticatedError("Outlook category change requires authentication.")
        if not message_id or not category_name:
            return message_id
        quoted = urllib.parse.quote(message_id, safe="")
        base_url = f"{GRAPH_BASE_URL}/me/messages/{quoted}"
        try:
            # Read the FRESH category list — never from a local snapshot.
            current = self._graph_request_json_with_retries(
                "GET", f"{base_url}?$select=categories", operation="get_categories",
            )
            categories = list(current.get("categories") or [])

            present = category_name in categories
            if add and present:
                return message_id
            if not add and not present:
                return message_id
            if add:
                categories.append(category_name)
            else:
                categories = [c for c in categories if c != category_name]

            patched = self._graph_request_json_with_retries(
                "PATCH", base_url, body={"categories": categories},
                operation="patch_categories",
            )
        except EmailExternalAPIError as exc:
            raise CategoryOperationError(
                "Outlook category read-modify-write failed while assigning a folder."
                if add
                else "Outlook category read-modify-write failed while removing a folder."
            ) from exc
        except Exception as exc:
            raise CategoryOperationError(
                f"Outlook unexpected category operation error ({type(exc).__name__}): {exc}"
            ) from exc
        return str(patched.get("id") or message_id)
