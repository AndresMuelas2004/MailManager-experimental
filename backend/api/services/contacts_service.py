"""Recipient-autocomplete service: aggregates known addresses from the
user's synced mail.

Local-only — no provider call. The suggestions are built entirely from
the ``email_metadata`` rows already pulled by the user's real mailboxes
(senders of received mail + recipients of sent mail), exactly like the
search-by-``q`` listing. There is no ownership check on external ids
because the endpoint receives none: the candidate accounts come from
``account_store.list_account_ids_by_user(user_id)``, so a user can never
see addresses sourced from accounts they do not own.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from api.errors.exceptions import RecipientSuggestionsError
from api.schemas.contact import ContactSuggestionOut
from api.services.services_helpers import parse_search_tokens, translate_database_error
from database import account_store, DatabaseError, email_metadata_store


def suggest_contacts(user_id: str, q: str, limit: int) -> list[ContactSuggestionOut]:
    """Return up to *limit* distinct address suggestions matching *q*
    across every account owned by *user_id*.

    Returns ``[]`` when *q* has no usable tokens (e.g. whitespace-only
    that still passed the router's ``min_length``) or the user owns no
    accounts — both without touching the email-metadata store.
    """
    tokens = parse_search_tokens(q)
    if not tokens:
        return []

    try:
        owned_account_ids = account_store.list_account_ids_by_user(user_id)
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected error listing owned accounts while building recipient suggestions (%s): %s",
            type(exc).__name__, exc,
        )
        raise RecipientSuggestionsError(
            "Failed to list owned accounts while building recipient suggestions."
        ) from exc
    if not owned_account_ids:
        return []

    try:
        rows = email_metadata_store.list_recipient_suggestions(
            owned_account_ids, tokens, limit,
        )
    except DatabaseError as exc:
        raise translate_database_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "Unexpected error querying recipient suggestions (%s): %s",
            type(exc).__name__, exc,
        )
        raise RecipientSuggestionsError(
            "Failed to query recipient suggestions across the user's accounts."
        ) from exc

    return [
        ContactSuggestionOut(email=row["email"], name=(row.get("name") or None))
        for row in rows
    ]
