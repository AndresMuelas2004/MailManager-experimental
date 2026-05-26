"""
SQL statements for the ``virtual_mailboxes`` table.

Virtual mailboxes are user-defined filtered views over ``email_metadata``
(see ``backend/database/migrations/versions/0028_*``). The actual
filtering happens by translating ``scope_payload`` + ``filter_payload``
into ``email_metadata`` predicates at read time — those live in
``email_metadata.py``. This module only handles the CRUD over the
``virtual_mailboxes`` rows themselves.
"""

from __future__ import annotations


INSERT_VIRTUAL_MAILBOX = """
    INSERT INTO virtual_mailboxes
        (virtual_mailbox_id, owner_user_id, display_name,
         scope_kind, scope_payload, filter_payload)
    VALUES
        (%(virtual_mailbox_id)s, %(owner_user_id)s, %(display_name)s,
         %(scope_kind)s, %(scope_payload)s::jsonb, %(filter_payload)s::jsonb)
    RETURNING virtual_mailbox_id, owner_user_id, display_name,
              scope_kind, scope_payload, filter_payload,
              created_at, updated_at
"""

GET_VIRTUAL_MAILBOX = """
    SELECT virtual_mailbox_id, owner_user_id, display_name,
           scope_kind, scope_payload, filter_payload,
           created_at, updated_at
    FROM virtual_mailboxes
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
"""

LIST_VIRTUAL_MAILBOXES_BY_OWNER = """
    SELECT virtual_mailbox_id, owner_user_id, display_name,
           scope_kind, scope_payload, filter_payload,
           created_at, updated_at
    FROM virtual_mailboxes
    WHERE owner_user_id = %(owner_user_id)s
    ORDER BY created_at DESC
"""

# Full-field replacement. The service layer always loads the row first
# (so it can apply field-level fallbacks before issuing the UPDATE), so
# there is no need for partial updates here. ``updated_at`` is bumped
# unconditionally — caller does not pass it.
UPDATE_VIRTUAL_MAILBOX = """
    UPDATE virtual_mailboxes
    SET display_name   = %(display_name)s,
        scope_kind     = %(scope_kind)s,
        scope_payload  = %(scope_payload)s::jsonb,
        filter_payload = %(filter_payload)s::jsonb,
        updated_at     = now()
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
    RETURNING virtual_mailbox_id, owner_user_id, display_name,
              scope_kind, scope_payload, filter_payload,
              created_at, updated_at
"""

DELETE_VIRTUAL_MAILBOX = """
    DELETE FROM virtual_mailboxes
    WHERE virtual_mailbox_id = %(virtual_mailbox_id)s
"""
