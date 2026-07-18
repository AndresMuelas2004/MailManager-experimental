"""
SQL for the internal rule engine (carpetas-y-reglas).

A rule is a user-level condition (exact ``from_email`` and/or ``subject``
substring — at least one, CHECK-enforced at the table) plus an action (assign
the matched message to ``target_folder_id``). Rules are evaluated by MISSELA at
sync time; there are NO native Gmail Filters / Outlook messageRules (those would
require prohibited scopes).

The query that SELECTs the messages a rule matches lives on the email-metadata
store (``email_metadata.LIST_MESSAGES_MATCHING_RULE``) because it reads
``email_metadata`` and reuses that layer's escaping helpers — not here.
"""

from __future__ import annotations


_RULE_FIELDS = (
    "rule_id, owner_user_id, name, is_enabled, match_from_email, "
    "match_subject_contains, target_folder_id, created_at, updated_at"
)


INSERT_RULE = f"""
    INSERT INTO rules (
        rule_id, owner_user_id, name, is_enabled,
        match_from_email, match_subject_contains, target_folder_id
    )
    VALUES (
        %(rule_id)s, %(owner_user_id)s, %(name)s, %(is_enabled)s,
        %(match_from_email)s, %(match_subject_contains)s, %(target_folder_id)s
    )
    RETURNING {_RULE_FIELDS}
"""

GET_RULE = f"""
    SELECT {_RULE_FIELDS}
    FROM rules
    WHERE rule_id = %(rule_id)s
"""

LIST_RULES_BY_OWNER = f"""
    SELECT {_RULE_FIELDS}
    FROM rules
    WHERE owner_user_id = %(owner_user_id)s
    ORDER BY created_at DESC
"""

# Active rules of a user — the sync-time evaluation loads only these.
LIST_ACTIVE_RULES_BY_OWNER = f"""
    SELECT {_RULE_FIELDS}
    FROM rules
    WHERE owner_user_id = %(owner_user_id)s
      AND is_enabled = TRUE
"""

# Full-field replace. The service loads the row first (ownership + merges the
# PATCH delta), so every column is passed explicitly. ``updated_at`` bumped
# unconditionally. A ``None`` row (deleted between pre-check and UPDATE) surfaces
# as 404.
UPDATE_RULE = f"""
    UPDATE rules
    SET name                   = %(name)s,
        is_enabled             = %(is_enabled)s,
        match_from_email       = %(match_from_email)s,
        match_subject_contains = %(match_subject_contains)s,
        target_folder_id       = %(target_folder_id)s,
        updated_at             = now()
    WHERE rule_id = %(rule_id)s
    RETURNING {_RULE_FIELDS}
"""

DELETE_RULE = """
    DELETE FROM rules
    WHERE rule_id = %(rule_id)s
"""
