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

# Thread-wide read-status update for the conversation viewer. Marks every row
# whose id is in ``message_ids`` OR whose ``thread_id`` matches the thread of
# any of those ids. The thread branch is what covers Outlook's silent
# duplicate rows: Outlook hands the same physical message different REST ids on
# different endpoints/calls, so the sync (folder delta) and the conversation
# (mailbox-wide $filter) persist separate rows for one message. A per-id update
# leaves the twin's ``is_read`` stale, and the grouped listing's
# ``bool_and(is_read)`` then keeps the thread bold. Threading is derived from
# the ids' own rows, so the caller only needs the opened (listing) id.
UPDATE_READ_STATUS_BY_THREAD = """
    UPDATE email_metadata AS em
       SET is_read = %(is_read)s
     WHERE em.account_id = %(account_id)s::UUID
       AND (
             em.provider_message_id = ANY(%(message_ids)s)
          OR em.thread_id IN (
                 SELECT sub.thread_id
                   FROM email_metadata AS sub
                  WHERE sub.account_id            = %(account_id)s::UUID
                    AND sub.provider_message_id   = ANY(%(message_ids)s)
                    AND sub.thread_id            <> ''
             )
       )
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

# Ghost-email reconciliation: only the stored ids NOT present in the
# bootstrap set. Pushing the set-difference into SQL keeps the result bounded
# to the suspect rows instead of loading every provider_message_id of the
# account into Python (a full-sync of a large account could be tens of
# thousands). The ``::text[]`` cast lets an empty exclude list bind cleanly
# (``!= ALL('{}')`` is TRUE for every row → every stored id is a suspect).
LIST_PROVIDER_MESSAGE_IDS_NOT_IN = """
    SELECT provider_message_id
    FROM email_metadata
    WHERE account_id = %(account_id)s
      AND provider_message_id != ALL(%(exclude_ids)s::text[])
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

# {box_predicate}, {search_predicate}, {extra_predicate} and {order_by}
# are Python str.format() slots populated by
# PgEmailMetadataStore.list_filtered. The three predicate slots each
# expand to either an empty string or " AND (...)" — only parameterised
# clauses (%(name)s) belong inside. The {order_by} slot expands to a
# trusted ORDER BY body built by ``_build_order_by`` from the closed
# ``_SORT_EXPRESSIONS`` whitelist — never raw user text.
#
# A FIFTH slot, {match_predicate}, exists ONLY in the *grouped* templates
# (LIST_/COUNT_GROUPED_BY_THREAD[_DISTINCT]). It carries the SAME
# search+extra/operator conditions as a single boolean EXPRESSION (no
# leading " AND ", defaulting to the literal ``TRUE``) so a thread can be
# surfaced when ANY of its messages matches — see the MATCH-SURFACING note
# on those templates. The flat (non-grouped) templates here do NOT carry it;
# str.format() simply ignores the unused keyword.
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
#
# ORDER-BY ALIAS NOTE: the ``{order_by}`` body references columns WITHOUT
# an alias prefix (``received_at``, ``from_name``, ``subject``, …). Here
# the FROM is ``email_metadata AS em JOIN accounts AS a USING (account_id)``:
# those columns exist ONLY in ``em`` (``accounts`` has none of them) and
# ``account_id`` is the USING-merged column, so every unprefixed reference
# resolves unambiguously to ``em.*``. The three subquery-based templates
# below expose the same columns through their single outer alias ``d``, so
# the SAME unprefixed body is valid there too — one ``_build_order_by``
# serves all four templates.
LIST_FILTERED = """
    SELECT em.provider_message_id, em.account_id, em.thread_id, em.from_email,
           em.from_name, em.subject, em.received_at, em.is_read, em.box,
           em.has_attachments, em.is_favorite, em.to_email, em.to_name,
           a.mailbox_id
    FROM email_metadata AS em
    JOIN accounts AS a USING (account_id)
    WHERE em.account_id = ANY(%(account_ids)s::uuid[])
      {box_predicate}
      {search_predicate}
      {extra_predicate}
    ORDER BY {order_by}
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

# Row count for the SAME filtered set ``LIST_FILTERED`` lists, WITHOUT
# ``ORDER BY`` / ``LIMIT`` / ``OFFSET``. Backs the ``total`` of the
# paginated listing envelope (``EmailPageOut``). The three slots are
# formatted by the SAME ``_build_filter_predicates`` helper that feeds
# ``LIST_FILTERED`` — so the COUNT counts exactly what the SELECT lists.
# The JOIN with ``accounts`` is retained even though no current predicate
# references ``a.*`` (the only JOIN use in ``LIST_FILTERED`` is projecting
# ``a.mailbox_id`` in the SELECT, which a COUNT does not need): it keeps
# the predicate strings identical across SELECT and COUNT so a future
# filter on ``a.*`` cannot make the COUNT diverge silently. The cost of a
# JOIN over the indexed PK is negligible on the bounded synced window.
COUNT_FILTERED = """
    SELECT COUNT(*) AS total
    FROM email_metadata AS em
    JOIN accounts AS a USING (account_id)
    WHERE em.account_id = ANY(%(account_ids)s::uuid[])
      {box_predicate}
      {search_predicate}
      {extra_predicate}
"""

# Unread-message counter (feature: contador de no leídos). Counts UNREAD
# (``is_read = FALSE``) messages per account for a single box, across the
# given accounts, in one ``GROUP BY account_id`` round trip. The service
# sums the rows for the mailbox-wide ``total`` and fills 0 for accounts
# with no unread rows (a ``GROUP BY`` emits no row for them). Local-only
# (no provider call). Counts INDIVIDUAL messages — it does NOT group by
# thread, by product decision (see docs/features/contador-no-leidos.md).
#
# ``box`` is a single trusted value constrained at the API boundary to
# ALL_MAIL | SPAM; it is bound as a parameter, never interpolated.
# ``account_ids`` is cast to uuid[] exactly like LIST_FILTERED. No new
# index — ``idx_email_metadata_account_id`` only narrows by account_id
# (``is_read`` / ``box`` remain heap post-filters, not index-covered),
# which suffices for the bounded synced volume (same MVP trade-off as the
# filtered listings).
COUNT_UNREAD_BY_ACCOUNT = """
    SELECT account_id, COUNT(*) AS unread
    FROM email_metadata
    WHERE account_id = ANY(%(account_ids)s::uuid[])
      AND is_read = FALSE
      AND box = %(box)s
    GROUP BY account_id
"""

# Deduplicated row count for virtual mailboxes. ``COUNT(DISTINCT
# provider_message_id)`` collapses the same provider message surfaced
# under two ``account_id`` rows (one provider account connected under two
# mailboxes) into one — matching ``LIST_FILTERED_DISTINCT``. No inner
# ``ORDER BY`` is needed: counting distinct keys does not require the
# completeness tie-break that picks WHICH duplicate row wins.
COUNT_FILTERED_DISTINCT = """
    SELECT COUNT(DISTINCT em.provider_message_id) AS total
    FROM email_metadata AS em
    JOIN accounts AS a USING (account_id)
    WHERE em.account_id = ANY(%(account_ids)s::uuid[])
      {box_predicate}
      {search_predicate}
      {extra_predicate}
"""

# Deduplicated + paginated listing for virtual mailboxes (a vmbox can
# aggregate accounts across different real mailboxes, so the SAME
# provider message may appear twice). The dedup MUST happen in SQL,
# before LIMIT/OFFSET, otherwise paging by position skips/duplicates rows
# at the page borders and a plain COUNT over-counts the duplicates.
#
# The inner ``DISTINCT ON (em.provider_message_id)`` + inner ``ORDER BY``
# replicate exactly the winner-selection that the removed Python
# ``_dedupe_rows_by_provider_message_id`` used: a populated ``to_email``
# beats an empty one, then a populated ``to_name``, then the most recent
# ``received_at``. ``btrim(coalesce(col, ''))`` is load-bearing parity with
# the Python ``isinstance(str) and col.strip()`` test that scored NULL,
# ``''`` AND whitespace-only strings as empty: a plain ``col <> ''`` would
# diverge twice — ``NULL <> ''`` is NULL (orders differently from FALSE
# under DESC) and ``'  ' <> ''`` is TRUE (the Python treats it as empty).
# The column is ``VARCHAR NOT NULL DEFAULT ''`` since migration 0031 so a
# migrated DB carries no NULLs, but the coalesce keeps parity for any
# pre-0031 row and is free.
#
# The OUTER ``ORDER BY`` restores presentation order (received_at DESC)
# and ADDS the PK tie-break the Python sort lacked, making OFFSET paging
# total/stable — a strict improvement, not a behaviour change.
#
# {box_predicate}/{search_predicate}/{extra_predicate} are the SAME slots
# fed by ``_build_filter_predicates`` — never inject free-form text.
LIST_FILTERED_DISTINCT = """
    SELECT * FROM (
        SELECT DISTINCT ON (em.provider_message_id)
               em.provider_message_id, em.account_id, em.thread_id, em.from_email,
               em.from_name, em.subject, em.received_at, em.is_read, em.box,
               em.has_attachments, em.is_favorite, em.to_email, em.to_name,
               a.mailbox_id
        FROM email_metadata AS em
        JOIN accounts AS a USING (account_id)
        WHERE em.account_id = ANY(%(account_ids)s::uuid[])
          {box_predicate}
          {search_predicate}
          {extra_predicate}
        ORDER BY em.provider_message_id,
                 (btrim(coalesce(em.to_email, '')) <> '') DESC,
                 (btrim(coalesce(em.to_name,  '')) <> '') DESC,
                 em.received_at DESC NULLS LAST
    ) AS d
    ORDER BY {order_by}
    LIMIT %(limit)s
    OFFSET %(offset)s
"""

# ---------------------------------------------------------------------------
# Conversation grouping (conversation view). Each thread collapses into one
# representative row (its most-recent message). The aggregated fields are
# window functions over the grouping key; the COUNT of messages-in-this-box
# is ``count(*) OVER w`` (PostgreSQL forbids ``COUNT(DISTINCT …) OVER (…)``).
#
# Grouping-key asymmetry (deliberate — provider thread namespaces are
# per-account, so accounts never merge; see repository_guide.md / the
# functional spec "accounts are not merged"):
#   - REGULAR (single account or unified): partition by (account_id,
#     thread_key). Including account_id keeps two DISTINCT accounts under
#     the same unified mailbox separate even in the pathological case where
#     they share a thread_id string.
#   - VIRTUAL: partition by thread_key alone, AFTER an inner dedup by
#     provider_message_id (subnivel d1). This collapses the SAME provider
#     account connected under two mailboxes (same provider_message_id, same
#     thread_id) while genuinely distinct accounts stay separated by their
#     per-namespace thread_id.
#
# thread_key = COALESCE(NULLIF(thread_id, ''), provider_message_id):
#   threadless messages ('' / NULL thread_id) become singletons keyed by
#   their own provider_message_id and are NEVER merged with one another.
#
# The three predicate slots are the SAME ones fed by
# ``_build_filter_predicates`` — never inject free-form text.

# REGULAR grouped listing (distinct_provider_message_id = False). No
# cross-account duplicate can occur (each account appears once in its
# mailbox), so a plain ``count(*)`` per partition is exact. The DISTINCT ON
# picks the most-recent message of each (account_id, thread_key) as the
# row representative; window aggregates are evaluated BEFORE the DISTINCT ON
# so the representative already carries the whole-thread aggregates.
#
# MATCH-SURFACING (the search/chip slots are NOT in the inner WHERE here):
# this REGULAR grouped template backs the regular ``GET /emails`` listing,
# whose free-text search + quick-filter chips / lupa operators must surface a
# thread when ANY of its messages matches, while keeping the representative as
# the thread's most-recent message and ``thread_message_count`` as the whole
# thread present in the box (docs/features/ordenar-y-filtrar-listado.md § 5,
# conversaciones.md, repository_guide.md § 2.6). So ONLY the ``{box_predicate}``
# stays in the inner WHERE (it defines which messages belong to the
# thread-in-this-box and hence the count); the search + extra clauses are
# folded into the ``{match_predicate}`` boolean expression (``TRUE`` when there
# is no search/chip), windowed with ``bool_or`` over the partition, and the
# outer wrapper keeps only threads where that OR is true. A row-level WHERE on
# those predicates (the pre-fix behaviour) silently dropped non-matching
# siblings from the partition, corrupting both the representative (it became
# the most-recent *matching* row) and the count.
#
# ⚠️ ASYMMETRY with the *_DISTINCT (virtual-mailbox) sibling: the vmbox
# listing does the OPPOSITE — its saved filter_payload + q operators DEFINE
# the bandeja's content and filter message-by-message BEFORE grouping (no
# match-surfacing), so that template keeps the search/extra clauses in the
# inner WHERE and has no ``{match_predicate}`` slot. The chips/sort feature
# explicitly does not touch the fake bandejas (ordenar-y-filtrar-listado.md
# § 2). Do NOT unify the two templates — the difference is the contract.
LIST_GROUPED_BY_THREAD = """
    SELECT * FROM (
        SELECT DISTINCT ON (em.account_id, COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id))
               em.provider_message_id, em.account_id, em.thread_id, em.from_email,
               em.from_name, em.subject, em.received_at,
               bool_and(em.is_read)        OVER w AS is_read,
               em.box,
               bool_or(em.has_attachments) OVER w AS has_attachments,
               bool_or(em.is_favorite)     OVER w AS is_favorite,
               em.to_email, em.to_name, a.mailbox_id,
               count(*)                    OVER w AS thread_message_count,
               bool_or({match_predicate})  OVER w AS thread_matches
        FROM email_metadata AS em
        JOIN accounts AS a USING (account_id)
        WHERE em.account_id = ANY(%(account_ids)s::uuid[])
          {box_predicate}
        WINDOW w AS (PARTITION BY em.account_id, COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id))
        ORDER BY em.account_id,
                 COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id),
                 em.received_at DESC, em.provider_message_id
    ) AS d
    WHERE d.thread_matches
    ORDER BY {order_by}
    LIMIT %(limit)s
    OFFSET %(offset)s
"""

# VIRTUAL grouped listing (distinct_provider_message_id = True). Three
# levels: (1) inner ``d1`` dedups by provider_message_id with the SAME
# winner-selection ORDER BY as ``LIST_FILTERED_DISTINCT`` (load-bearing
# parity), exposing a computed ``thread_key``; (2) group by thread_key with
# ``count(*)`` over the already-deduplicated rows; (3) paginate. Partitioning
# by thread_key alone (no account_id) is what collapses one provider account
# connected under two mailboxes — exactly the dedup intent of the virtual
# listing.
#
# NO MATCH-SURFACING here (asymmetry vs ``LIST_GROUPED_BY_THREAD``): the
# virtual-mailbox listing is the surface where the search ``q`` / lupa
# operators AND the saved ``filter_payload`` extra-filters DEFINE which
# messages belong to the fake bandeja, so they filter message-by-message
# BEFORE grouping (the representative is the matching message). This is the
# established vmbox behaviour (docs/features/bandejas-ficticias.md) and is
# deliberately different from the regular ``GET /emails`` listing, whose
# chips/search surface whole threads (ordenar-y-filtrar-listado.md § 5 —
# that feature explicitly does NOT touch the fake bandejas). Hence the
# search/extra clauses stay in the inner ``d1`` WHERE and there is no
# ``{match_predicate}`` slot in this template.
LIST_GROUPED_BY_THREAD_DISTINCT = """
    SELECT * FROM (
        SELECT DISTINCT ON (d1.thread_key)
               d1.provider_message_id, d1.account_id, d1.thread_id, d1.from_email,
               d1.from_name, d1.subject, d1.received_at,
               bool_and(d1.is_read)        OVER w AS is_read,
               d1.box,
               bool_or(d1.has_attachments) OVER w AS has_attachments,
               bool_or(d1.is_favorite)     OVER w AS is_favorite,
               d1.to_email, d1.to_name, d1.mailbox_id,
               count(*)                    OVER w AS thread_message_count
        FROM (
            SELECT DISTINCT ON (em.provider_message_id)
                   em.provider_message_id, em.account_id, em.thread_id, em.from_email,
                   em.from_name, em.subject, em.received_at, em.is_read, em.box,
                   em.has_attachments, em.is_favorite, em.to_email, em.to_name,
                   a.mailbox_id,
                   COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id) AS thread_key
            FROM email_metadata AS em
            JOIN accounts AS a USING (account_id)
            WHERE em.account_id = ANY(%(account_ids)s::uuid[])
              {box_predicate}
              {search_predicate}
              {extra_predicate}
            ORDER BY em.provider_message_id,
                     (btrim(coalesce(em.to_email, '')) <> '') DESC,
                     (btrim(coalesce(em.to_name,  '')) <> '') DESC,
                     em.received_at DESC NULLS LAST
        ) AS d1
        WINDOW w AS (PARTITION BY d1.thread_key)
        ORDER BY d1.thread_key, d1.received_at DESC, d1.account_id, d1.provider_message_id
    ) AS d
    ORDER BY {order_by}
    LIMIT %(limit)s
    OFFSET %(offset)s
"""

# Thread count for the REGULAR grouped listing — counts DISTINCT
# (account_id, thread_key) pairs, aligned with ``LIST_GROUPED_BY_THREAD``.
# ``COUNT(DISTINCT (a, b))`` over a row tuple is valid in PostgreSQL and
# counts distinct non-null combinations; account_id is never NULL and
# thread_key is never NULL (the COALESCE guarantees it), so no thread is
# dropped from the total. The COALESCE is mandatory: ``COUNT(DISTINCT
# thread_id)`` alone would skip every threadless ('' / NULL) message.
#
# MATCH-SURFACING (mirrors ``LIST_GROUPED_BY_THREAD``): the search + chip
# clauses do NOT filter rows in the inner WHERE — they would drop the whole
# thread when only some of its messages match. Only ``{box_predicate}``
# scopes the inner rows; the ``{match_predicate}`` boolean is OR'd over the
# (account_id, thread_key) window and the outer query counts only threads
# with at least one match, so ``total`` counts EXACTLY the threads the LIST
# surfaces. When there is no search/chip ``{match_predicate}`` is ``TRUE``,
# so ``thread_matches`` is true for every thread and the count reduces to
# the pre-fix DISTINCT-pair total.
COUNT_GROUPED_BY_THREAD = """
    SELECT COUNT(*) AS total FROM (
        SELECT DISTINCT ON (em.account_id, COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id))
               bool_or({match_predicate}) OVER (
                   PARTITION BY em.account_id,
                                COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id)
               ) AS thread_matches
        FROM email_metadata AS em
        JOIN accounts AS a USING (account_id)
        WHERE em.account_id = ANY(%(account_ids)s::uuid[])
          {box_predicate}
    ) AS t
    WHERE t.thread_matches
"""

# Thread count for the VIRTUAL grouped listing — counts DISTINCT thread_key,
# aligned with ``LIST_GROUPED_BY_THREAD_DISTINCT`` (which collapses one
# provider account connected under two mailboxes). No inner dedup is needed:
# the same provider_message_id under two account_ids carries the SAME
# thread_key (shared thread_id, or shared provider_message_id when
# threadless), so ``COUNT(DISTINCT thread_key)`` collapses it exactly as the
# LIST does. The COALESCE is mandatory for the same threadless reason above.
# Like its LIST sibling, this surface filters message-by-message BEFORE
# grouping (no match-surfacing) — see the LIST header for the asymmetry.
COUNT_GROUPED_BY_THREAD_DISTINCT = """
    SELECT COUNT(DISTINCT COALESCE(NULLIF(em.thread_id, ''), em.provider_message_id)) AS total
    FROM email_metadata AS em
    JOIN accounts AS a USING (account_id)
    WHERE em.account_id = ANY(%(account_ids)s::uuid[])
      {box_predicate}
      {search_predicate}
      {extra_predicate}
"""

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

# Conversation lazy-sync favourites: mark a SUBSET of an account's rows
# (the thread members the provider reports as favourite) TRUE in a single
# statement. Unlike SYNC_FAVORITES_FOR_ACCOUNT it does NOT touch rows
# outside ``true_ids`` — the conversation sync only knows the thread it
# just fetched, so it must never clear favourites elsewhere in the account.
# One-directional by design (never sets FALSE).
UPDATE_FAVORITES_TRUE_BATCH = """
    UPDATE email_metadata
    SET is_favorite = TRUE
    WHERE account_id = %(account_id)s
      AND provider_message_id = ANY(%(true_ids)s)
"""

EXISTS_BY_MESSAGE_ID = """
    SELECT 1 FROM email_metadata
    WHERE provider_message_id = %(provider_message_id)s
      AND account_id = %(account_id)s
    LIMIT 1
"""

# Single-row read of a message's full metadata (incl. thread_id), with the
# EXACT column list + JOIN of ``LIST_FILTERED`` so ``row_to_email_metadata_out``
# maps it with no special-casing. Backs the conversation endpoint's base-message
# lookup: the row yields both the ``thread_id`` (to fetch the thread) and the
# presentation columns (to map the singleton EmailMetadataOut when thread_id is
# empty), avoiding a second read. No ``LIMIT 1``: ``(provider_message_id,
# account_id)`` is the table's primary key, so the WHERE matches at most one
# row by definition (the JOIN on the unique ``accounts.account_id`` cannot
# multiply it) — the uniqueness comes from the schema, not a defensive limit.
GET_METADATA_BY_MESSAGE = """
    SELECT em.provider_message_id, em.account_id, em.thread_id, em.from_email,
           em.from_name, em.subject, em.received_at, em.is_read, em.box,
           em.has_attachments, em.is_favorite, em.to_email, em.to_name,
           a.mailbox_id
    FROM email_metadata AS em
    JOIN accounts AS a USING (account_id)
    WHERE em.account_id = %(account_id)s
      AND em.provider_message_id = %(provider_message_id)s
"""

# Recipient-autocomplete aggregation (user-level; no provider call).
# Two UNION ALL branches collect candidate (email, name, received_at)
# tuples: senders of received mail (from_*) and recipients of sent mail
# (to_*), both restricted to boxes other than SPAM/TRASH/DELETED. The
# outer SELECT dedupes by lower(email), picks the most-recent non-empty
# name, counts frequency and tracks recency, excludes the user's own
# account addresses, and orders by frequency then recency. The
# {from_token_predicate}/{to_token_predicate} slots are filled by the
# repository from a hardcoded column whitelist + named params — NEVER
# inject free-form text (same SQL-injection invariant as LIST_FILTERED).
#
# Subscript trap (do NOT "simplify"): PostgreSQL cannot subscript the
# result of a function call directly (``func(...)[1]`` is a syntax
# error), so ``(array_remove(array_agg(...), NULL))[1]`` MUST keep the
# outer parentheses before ``[1]``. Removing them breaks the query.
LIST_RECIPIENT_SUGGESTIONS = """
    WITH candidates AS (
        SELECT from_email AS email, from_name AS name, received_at
        FROM email_metadata
        WHERE account_id = ANY(%(account_ids)s::uuid[])
          AND box NOT IN ('SPAM', 'TRASH', 'DELETED')
          AND btrim(coalesce(from_email, '')) <> ''
          {from_token_predicate}
        UNION ALL
        SELECT to_email AS email, to_name AS name, received_at
        FROM email_metadata
        WHERE account_id = ANY(%(account_ids)s::uuid[])
          AND box NOT IN ('SPAM', 'TRASH', 'DELETED')
          AND btrim(coalesce(to_email, '')) <> ''
          {to_token_predicate}
    )
    SELECT
        lower(c.email) AS email,
        (array_remove(
            array_agg(nullif(btrim(c.name), '') ORDER BY c.received_at DESC),
            NULL
        ))[1] AS name,
        count(*)           AS frequency,
        max(c.received_at) AS last_seen
    FROM candidates c
    WHERE lower(c.email) NOT IN (
        SELECT lower(a.email_address)
        FROM accounts a
        WHERE a.account_id = ANY(%(account_ids)s::uuid[])
          AND a.email_address IS NOT NULL
    )
    GROUP BY lower(c.email)
    ORDER BY frequency DESC, last_seen DESC, email ASC
    LIMIT %(limit)s
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

# Content-prefetch target selection. Picks the unread, recent (<=48h),
# inbox (``box='ALL_MAIL'`` — ``_resolve_labels`` maps INBOX -> ALL_MAIL)
# messages whose body is NOT yet cached, most-recent first, capped at
# ``limit``. The LEFT JOIN + ``ec.provider_message_id IS NULL`` excludes
# rows already present in ``email_content`` (those are served from cache,
# never re-fetched here). No new index for the MVP volume:
# ``idx_email_metadata_account_id`` narrows by account_id (``is_read`` /
# ``box`` stay heap post-filters) and ``idx_email_metadata_received_at`` can
# serve the ``ORDER BY received_at DESC LIMIT`` — neither index covers the
# full account+is_read+box predicate, an accepted trade-off at MVP scale.
LIST_UNREAD_RECENT_UNCACHED = """
    SELECT em.provider_message_id
    FROM email_metadata em
    LEFT JOIN email_content ec
      ON ec.account_id = em.account_id
     AND ec.provider_message_id = em.provider_message_id
    WHERE em.account_id = %(account_id)s
      AND em.is_read = FALSE
      AND em.box = 'ALL_MAIL'
      AND em.received_at >= (now() - INTERVAL '48 hours')
      AND ec.provider_message_id IS NULL
    ORDER BY em.received_at DESC
    LIMIT %(limit)s
"""
