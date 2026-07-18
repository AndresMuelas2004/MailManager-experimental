"""
SQL for the user-folder feature (carpetas-y-reglas).

Covers three tables:
- ``folders`` — the user-owned organisation label (CRUD).
- ``folder_account_links`` — the per-account provider materialisation
  (Gmail label id / Outlook category name), created lazily.
- ``email_folder_members`` — the multi-membership rows (assign / unassign /
  reconcile / chips).

The provider (Gmail label / Outlook category) is authoritative for membership,
exactly like ``is_favorite``: each sync reconciles ``email_folder_members``
against the labels/categories the provider reports, but ONLY for the folders
MISSELA manages for that account (via ``folder_account_links``).
"""

from __future__ import annotations


_FOLDER_FIELDS = (
    "folder_id, owner_user_id, name, color, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# folders CRUD
# ---------------------------------------------------------------------------

INSERT_FOLDER = f"""
    INSERT INTO folders (folder_id, owner_user_id, name, color)
    VALUES (%(folder_id)s, %(owner_user_id)s, %(name)s, %(color)s)
    RETURNING {_FOLDER_FIELDS}
"""

GET_FOLDER = f"""
    SELECT {_FOLDER_FIELDS}
    FROM folders
    WHERE folder_id = %(folder_id)s
"""

LIST_FOLDERS_BY_OWNER = f"""
    SELECT {_FOLDER_FIELDS}
    FROM folders
    WHERE owner_user_id = %(owner_user_id)s
    ORDER BY lower(name)
"""

# Full-field replace (rename / recolour). The service loads the row first for
# the ownership pre-check, so a partial update is unnecessary. ``updated_at``
# is bumped unconditionally. A ``None`` row (race: deleted between pre-check and
# UPDATE) surfaces as 404 in the service.
UPDATE_FOLDER = f"""
    UPDATE folders
    SET name       = %(name)s,
        color      = %(color)s,
        updated_at = now()
    WHERE folder_id = %(folder_id)s
    RETURNING {_FOLDER_FIELDS}
"""

DELETE_FOLDER = """
    DELETE FROM folders
    WHERE folder_id = %(folder_id)s
"""


# ---------------------------------------------------------------------------
# folder_account_links (lazy per-account materialisation)
# ---------------------------------------------------------------------------

# Lazy materialisation: created the first time a message of that account is
# assigned to the folder (or during "apply to existing"). ``ON CONFLICT`` keeps
# the existing ``provider_ref`` (Gmail label ids never change; an Outlook rename
# re-syncs the ref via UPDATE_LINK_REF), so re-materialising is idempotent.
UPSERT_LINK = """
    INSERT INTO folder_account_links (folder_id, account_id, provider_ref)
    VALUES (%(folder_id)s, %(account_id)s, %(provider_ref)s)
    ON CONFLICT (folder_id, account_id) DO NOTHING
"""

GET_LINK = """
    SELECT folder_id, account_id, provider_ref
    FROM folder_account_links
    WHERE folder_id = %(folder_id)s AND account_id = %(account_id)s
"""

# Every account materialisation of a folder — drives the rename/delete
# reflection loop (the service walks each link and re-tags / drops the label or
# category in that account).
LIST_LINKS_BY_FOLDER = """
    SELECT folder_id, account_id, provider_ref
    FROM folder_account_links
    WHERE folder_id = %(folder_id)s
"""

# The {provider_ref -> folder_id} map of an account, used by sync reconciliation
# to cross the provider's raw labelIds / categories against the folders MISSELA
# manages for that account. Empty result → nothing to reconcile.
LIST_LINKS_BY_ACCOUNT = """
    SELECT folder_id, provider_ref
    FROM folder_account_links
    WHERE account_id = %(account_id)s
"""

# Outlook category rename re-points the link's provider_ref to the new category
# name (Gmail keeps the same opaque label id, so this only fires for Outlook).
UPDATE_LINK_REF = """
    UPDATE folder_account_links
    SET provider_ref = %(provider_ref)s
    WHERE folder_id = %(folder_id)s AND account_id = %(account_id)s
"""


# ---------------------------------------------------------------------------
# email_folder_members (the membership itself)
# ---------------------------------------------------------------------------

ADD_MEMBER = """
    INSERT INTO email_folder_members (provider_message_id, account_id, folder_id)
    VALUES (%(provider_message_id)s, %(account_id)s, %(folder_id)s)
    ON CONFLICT (provider_message_id, account_id, folder_id) DO NOTHING
"""

REMOVE_MEMBER = """
    DELETE FROM email_folder_members
    WHERE provider_message_id = %(provider_message_id)s
      AND account_id          = %(account_id)s
      AND folder_id           = %(folder_id)s
"""

# Batch upsert of the folders present on a synced message (from provider labels)
# — the "keep present" half of reconciliation. ``execute_values`` fills ``%s``.
# The ``WHERE EXISTS`` guard is load-bearing: an incremental label-update can
# reference a message that is NOT in our local copy (a label change on a message
# beyond our sync window), and a plain INSERT of that pair would raise a
# composite-FK violation that fails the WHOLE batch. The guard skips such orphan
# rows so one un-synced message never poisons the reconciliation of the rest.
UPSERT_MEMBERS_BATCH = """
    INSERT INTO email_folder_members (provider_message_id, account_id, folder_id)
    SELECT v.pmid, v.aid::uuid, v.fid::uuid
    FROM (VALUES %s) AS v(pmid, aid, fid)
    WHERE EXISTS (
        SELECT 1 FROM email_metadata em
        WHERE em.provider_message_id = v.pmid
          AND em.account_id = v.aid::uuid
    )
    ON CONFLICT (provider_message_id, account_id, folder_id) DO NOTHING
"""

# The "delete absent" half of reconciliation: for the messages SEEN in this sync
# (``seen_pmids``) and the folders MANAGED for this account (``managed_folder_ids``),
# drop any membership that the provider no longer reports (not in the present
# ``(pmid, folder_id)`` set). Scoped to seen messages so a partial sync never
# touches messages it did not observe, and to managed folders so an unmanaged
# label/category can never be interpreted as a folder (decision 12).
DELETE_ABSENT_MEMBERS = """
    DELETE FROM email_folder_members m
    WHERE m.account_id          = %(account_id)s
      AND m.provider_message_id = ANY(%(seen_pmids)s::varchar[])
      AND m.folder_id           = ANY(%(managed_folder_ids)s::uuid[])
      AND NOT EXISTS (
          SELECT 1
          FROM unnest(%(present_pmids)s::varchar[], %(present_folder_ids)s::uuid[]) AS p(pmid, fid)
          WHERE p.pmid = m.provider_message_id
            AND p.fid  = m.folder_id
      )
"""

# Member message ids of a folder in one account — drives the Outlook rename /
# delete reflection (categories are immutable, so a rename/delete re-tags each
# member; Gmail does it in one label call instead).
LIST_MEMBER_MESSAGE_IDS_BY_FOLDER_ACCOUNT = """
    SELECT provider_message_id
    FROM email_folder_members
    WHERE folder_id = %(folder_id)s AND account_id = %(account_id)s
"""

# Folder chips for a page of messages: every folder each ``(provider_message_id,
# account_id)`` pair belongs to, in a single round trip. The unnest builds the
# page's pair set; the service groups the rows by (pmid, account_id).
LIST_FOLDERS_FOR_MESSAGES = """
    SELECT p.pmid AS provider_message_id, p.aid AS account_id,
           f.folder_id, f.name, f.color
    FROM unnest(%(pmids)s::varchar[], %(aids)s::uuid[]) AS p(pmid, aid)
    JOIN email_folder_members m
      ON m.provider_message_id = p.pmid AND m.account_id = p.aid
    JOIN folders f ON f.folder_id = m.folder_id
    ORDER BY lower(f.name)
"""
