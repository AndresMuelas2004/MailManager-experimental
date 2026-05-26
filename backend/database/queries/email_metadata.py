"""
Email metadata SQL statements.
"""

from __future__ import annotations

# NOTE: ``has_attachments`` is intentionally NOT in the column list nor in
# the ``DO UPDATE SET`` clause. The flag follows the **B.lazy** strategy
# (D-09): metadata sync paths leave it at its current value (defaults to
# FALSE on INSERT) and ``recompute_has_attachments`` writes it later, on
# the cache-miss branch of ``get_email_content`` once the attachments are
# discovered. Adding it here would silently override that helper and reset
# the flag on every sync — do not.
UPSERT_EMAIL_METADATA_BATCH = """
    INSERT INTO email_metadata
        (provider_message_id, account_id, thread_id, from_email, from_name,
         subject, received_at, is_read, box, to_email, to_name)
    VALUES %s
    ON CONFLICT (provider_message_id, account_id) DO UPDATE SET
        is_read = EXCLUDED.is_read,
        box = CASE
            WHEN email_metadata.box = 'DELETED' AND EXCLUDED.box = 'TRASH'
            THEN 'DELETED'
            ELSE EXCLUDED.box
        END,
        to_email = EXCLUDED.to_email,
        to_name = EXCLUDED.to_name
"""

DELETE_BATCH_BY_MESSAGE_IDS = """
    DELETE FROM email_metadata
    WHERE account_id = %(account_id)s
      AND provider_message_id = ANY(%(message_ids)s)
"""

UPDATE_LABELS_BATCH = """
    UPDATE email_metadata AS em
       SET is_read = v.is_read,
           box = CASE
               WHEN em.box = 'DELETED' AND v.box = 'TRASH'
               THEN 'DELETED'
               ELSE v.box
           END
      FROM (VALUES %s) AS v(provider_message_id, account_id, is_read, box)
     WHERE em.provider_message_id = v.provider_message_id::VARCHAR
       AND em.account_id          = v.account_id::UUID
"""

UPDATE_READ_STATUS_BATCH = """
    UPDATE email_metadata AS em
       SET is_read = v.is_read
      FROM (VALUES %s) AS v(provider_message_id, account_id, is_read)
     WHERE em.provider_message_id = v.provider_message_id::VARCHAR
       AND em.account_id          = v.account_id::UUID
"""

# Rewrites BOTH ``provider_message_id`` and ``box`` because Outlook
# reassigns its message id when a message is moved between Spam <-> Inbox
# (the new id replaces the old one and we cascade the change through
# ON UPDATE CASCADE). Hence the ``MOVE`` verb in the constant name —
# the operation is a spam-folder move, not a status flag flip.
MOVE_SPAM_BATCH = """
    UPDATE email_metadata AS em
       SET provider_message_id = v.new_message_id,
           box                 = v.new_box
      FROM (VALUES %s) AS v(old_message_id, account_id, new_message_id, new_box)
     WHERE em.provider_message_id = v.old_message_id::VARCHAR
       AND em.account_id          = v.account_id::UUID
"""

LIST_PROVIDER_MESSAGE_IDS_BY_ACCOUNT = """
    SELECT provider_message_id
    FROM email_metadata
    WHERE account_id = %(account_id)s
"""

GET_TRASH_EMAILS_BY_IDS = """
    SELECT provider_message_id, account_id, box, previous_box
    FROM email_metadata
    WHERE account_id = %(account_id)s
      AND provider_message_id = ANY(%(message_ids)s)
      AND box = 'TRASH'
"""

MARK_AS_DELETED_BATCH = """
    UPDATE email_metadata
    SET box = 'DELETED'
    WHERE account_id = %(account_id)s
      AND provider_message_id = ANY(%(message_ids)s)
      AND box = 'TRASH'
"""

# NOTE: ``RESTORE_FROM_TRASH_BATCH`` and ``RESTORE_FROM_TRASH_DISCOVERED_BATCH``
# are intentionally separate. Their value-shapes differ (3-tuple vs
# 4-tuple); merging them with ``COALESCE(v.discovered_box, em.previous_box,
# 'ALL_MAIL')`` would force every caller to construct uniform 4-tuples,
# rippling through unrelated services. The duplication here is bounded and
# the divergence in source-of-truth for the destination box (previous_box
# vs provider-discovered) is the actual reason both exist.
RESTORE_FROM_TRASH_BATCH = """
    UPDATE email_metadata AS em
    SET provider_message_id = v.new_message_id::VARCHAR,
        box = COALESCE(em.previous_box, 'ALL_MAIL'),
        previous_box = NULL
    FROM (VALUES %s) AS v(old_message_id, new_message_id, account_id)
    WHERE em.provider_message_id = v.old_message_id::VARCHAR
      AND em.account_id = v.account_id::UUID
      AND em.box = 'TRASH'
"""

RESTORE_FROM_TRASH_DISCOVERED_BATCH = """
    UPDATE email_metadata AS em
    SET provider_message_id = v.new_message_id::VARCHAR,
        box = v.discovered_box::VARCHAR,
        previous_box = NULL
    FROM (VALUES %s) AS v(old_message_id, new_message_id, account_id, discovered_box)
    WHERE em.provider_message_id = v.old_message_id::VARCHAR
      AND em.account_id = v.account_id::UUID
      AND em.box = 'TRASH'
"""

MOVE_TO_TRASH_BATCH = """
    UPDATE email_metadata AS em
    SET provider_message_id = v.new_message_id::VARCHAR,
        previous_box = em.box,
        box = 'TRASH'
    FROM (VALUES %s) AS v(old_message_id, new_message_id, account_id)
    WHERE em.provider_message_id = v.old_message_id::VARCHAR
      AND em.account_id = v.account_id::UUID
      AND em.box NOT IN ('TRASH', 'DELETED')
"""

# {box_predicate}, {search_predicate} and {extra_predicate} are Python
# str.format() slots populated by PgEmailMetadataStore.list_filtered.
# Each one expands to either an empty string or " AND (...)" — only
# parameterised clauses (%(name)s) belong inside.
#
# Single query backs BOTH the regular box listing (one box, mandatory)
# and the virtual-mailbox listing (zero, one or many boxes derived from
# the stored filter_payload). The caller passes the right slot text in
# each case; merging them avoids two near-identical queries drifting
# over time.
#
# INVARIANT: NOTHING outside this module may inject text into any of the
# slots. The repository builds the predicates from a fixed set of
# hardcoded SQL fragments matched against trusted column whitelists; any
# future caller that wants a new predicate must extend the whitelist
# there, not pass a string here. Allowing arbitrary text would be a SQL
# injection vector.
LIST_FILTERED = """
    SELECT provider_message_id, account_id, thread_id, from_email, from_name,
           subject, received_at, is_read, box, has_attachments, is_favorite,
           to_email, to_name
    FROM email_metadata
    WHERE account_id = ANY(%(account_ids)s::uuid[])
      {box_predicate}
      {search_predicate}
      {extra_predicate}
    ORDER BY received_at DESC, account_id, provider_message_id
    LIMIT %(limit)s
    OFFSET %(offset)s
"""
# NOTE: ``ORDER BY received_at DESC`` alone leaves ties non-deterministic.
# When two messages share an identical ``received_at`` (e.g. mass-sent
# notifications batched to the same second), PostgreSQL is free to return
# them in any order — so the same physical row can appear on adjacent
# OFFSET pages (or be skipped) under the SAME query. The composite key
# ``(account_id, provider_message_id)`` is unique across the table (it is
# the table's primary key) and breaks every possible tie, making the
# ordering total — paging by ``OFFSET`` becomes stable. Do NOT remove
# the secondary keys without replacing them with another total-ordering
# tie-break: a regression here re-introduces silent dup/skip between
# pages, which is invisible from a single-page test.

# Favourites toggle (single row, the API surface is one message at a time
# per the no-bulk MVP decision). Returns the affected provider_message_id
# so the service can detect "row not found" without a second roundtrip.
UPDATE_FAVORITE_STATUS = """
    UPDATE email_metadata
    SET is_favorite = %(is_favorite)s
    WHERE account_id = %(account_id)s
      AND provider_message_id = %(provider_message_id)s
    RETURNING provider_message_id
"""

# Favourites sync: full replacement of the favourite set for a given
# account. ``true_ids`` is the list of provider_message_ids that should
# be marked TRUE; every OTHER row of that account is forced to FALSE.
# Single statement — atomic from the caller's perspective and cheaper
# than two separate updates.
SYNC_FAVORITES_FOR_ACCOUNT = """
    UPDATE email_metadata
    SET is_favorite = (provider_message_id = ANY(%(true_ids)s))
    WHERE account_id = %(account_id)s
"""

EXISTS_BY_MESSAGE_ID = """
    SELECT 1 FROM email_metadata
    WHERE provider_message_id = %(provider_message_id)s
      AND account_id = %(account_id)s
    LIMIT 1
"""

# Recompute has_attachments from email_attachments (D-09). The
# subquery counts non-inline rows; ``COUNT(*) > 0`` is true if and
# only if at least one downloadable attachment row exists. Idempotent
# — calling it twice in a row is a no-op. The single helper
# (``recompute_has_attachments``) is the only writer to this column.
UPDATE_HAS_ATTACHMENTS = """
    UPDATE email_metadata AS em
    SET has_attachments = (
        SELECT COUNT(*) > 0
        FROM email_attachments a
        WHERE a.account_id          = em.account_id
          AND a.provider_message_id = em.provider_message_id
          AND a.is_inline           = FALSE
    )
    WHERE em.account_id          = %(account_id)s
      AND em.provider_message_id = %(provider_message_id)s
"""
