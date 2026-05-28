"""
SQL statements for the ``virtual_mailboxes`` table.

Virtual mailboxes are user-defined filtered views over ``email_metadata``
(see ``backend/database/migrations/versions/0028_*`` and ``0032_*``).
The actual filtering happens by translating the persisted
``account_ids`` snapshot + ``filter_payload`` into ``email_metadata``
predicates at read time — those live in ``email_metadata.py``. This
module only handles the CRUD over the ``virtual_mailboxes`` rows
themselves.

The ``account_ids`` list lives inside the ``scope_payload`` JSONB
column under the ``account_ids`` key. Every SELECT projects it out as a
materialised ``text[]`` so the service layer receives a flat list and
never has to know the JSONB shape — the column name is a historical
artifact preserved by migration 0032 to avoid a column rename.
"""

from __future__ import annotations


# Projection shared by every SELECT. The ``account_ids`` field is the
# JSONB array materialised into a ``text[]`` and surfaced as a list[str]
# by the repository.
_SELECT_FIELDS = """
    virtual_mailbox_id,
    owner_user_id,
    display_name,
    COALESCE(
        ARRAY(
            SELECT jsonb_array_elements_text(
                COALESCE(scope_payload->'account_ids', '[]'::jsonb)
            )
        ),
        ARRAY[]::text[]
    ) AS account_ids,
    filter_payload,
    created_at,
    updated_at
"""


INSERT_VIRTUAL_MAILBOX = f"""
    INSERT INTO virtual_mailboxes
        (virtual_mailbox_id, owner_user_id, display_name,
         scope_payload, filter_payload)
    VALUES
        (%(virtual_mailbox_id)s, %(owner_user_id)s, %(display_name)s,
         %(scope_payload)s::jsonb, %(filter_payload)s::jsonb)
    RETURNING {_SELECT_FIELDS}
"""

GET_VIRTUAL_MAILBOX = f"""
    SELECT {_SELECT_FIELDS}
    FROM virtual_mailboxes
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
"""

LIST_VIRTUAL_MAILBOXES_BY_OWNER = f"""
    SELECT {_SELECT_FIELDS}
    FROM virtual_mailboxes
    WHERE owner_user_id = %(owner_user_id)s
    ORDER BY created_at DESC
"""

# Full-field replacement. The service layer always loads the row first
# (so it can apply ownership checks before issuing the UPDATE), so
# there is no need for partial updates here. ``updated_at`` is bumped
# unconditionally — caller does not pass it.
UPDATE_VIRTUAL_MAILBOX = f"""
    UPDATE virtual_mailboxes
    SET display_name   = %(display_name)s,
        scope_payload  = %(scope_payload)s::jsonb,
        filter_payload = %(filter_payload)s::jsonb,
        updated_at     = now()
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
    RETURNING {_SELECT_FIELDS}
"""

DELETE_VIRTUAL_MAILBOX = """
    DELETE FROM virtual_mailboxes
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
"""
