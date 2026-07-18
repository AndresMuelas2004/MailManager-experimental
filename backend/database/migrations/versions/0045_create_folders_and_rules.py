"""
Create the user-folder + internal-rule tables (feature: carpetas-y-reglas).

Five new tables, all user-level:

- ``folders`` — a user-owned organisation label ("Universidad", "Facturas").
  Case-insensitive unique per user (``UNIQUE (owner_user_id, lower(name))``)
  so "Universidad"/"universidad" cannot both exist for one user.
- ``folder_account_links`` — the per-account materialisation of a folder in
  the provider (Gmail user-label id / Outlook category name), created lazily
  on first use in that account.
- ``email_folder_members`` — the multi-membership itself (N rows per message).
  The composite FK to ``email_metadata`` carries BOTH ``ON DELETE CASCADE`` AND
  ``ON UPDATE CASCADE``: Outlook rewrites ``provider_message_id`` on box moves,
  so the update-cascade keeps the membership attached (mirrors
  ``email_attachments``). ``provider_message_id`` is ``VARCHAR(255)`` to match
  ``email_metadata`` and let the composite FK line up by type.
- ``rules`` — the internal (MISSELA-side) rule engine. Condition: exact
  ``from_email`` and/or ``subject`` substring (at least one, CHECK-enforced);
  action: assign the message to ``target_folder_id``.
- ``rule_apply_jobs`` — the durable "apply to existing" queue, a clone of
  ``account_backfill_jobs`` / ``draft_sync_jobs`` but keyed BY RULE (not by
  account) since an apply spans every account of the user. ``owner_user_id`` is
  denormalised (no FK of its own — it cascades transitively via ``rules``) so
  the worker resolves the user's accounts without a JOIN.

No new OAuth scope is required: folders are Gmail user-labels / Outlook
categories, both covered by the existing ``gmail.modify`` / ``Mail.ReadWrite``.
"""
from __future__ import annotations

from alembic import op


revision = "0045_create_folders_and_rules"
down_revision = "0044_invalidate_email_content_cache_premailer_no_network"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            folder_id     UUID         PRIMARY KEY,
            owner_user_id UUID         NOT NULL
                          REFERENCES users(user_id) ON DELETE CASCADE,
            name          TEXT         NOT NULL,
            color         TEXT,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_owner_lower_name "
        "ON folders (owner_user_id, lower(name));"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_account_links (
            folder_id    UUID  NOT NULL
                         REFERENCES folders(folder_id) ON DELETE CASCADE,
            account_id   UUID  NOT NULL
                         REFERENCES accounts(account_id) ON DELETE CASCADE,
            provider_ref TEXT  NOT NULL,
            PRIMARY KEY (folder_id, account_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_folder_account_links_account "
        "ON folder_account_links (account_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_folder_members (
            provider_message_id VARCHAR(255) NOT NULL,
            account_id          UUID         NOT NULL,
            folder_id           UUID         NOT NULL
                                REFERENCES folders(folder_id) ON DELETE CASCADE,
            PRIMARY KEY (provider_message_id, account_id, folder_id),
            FOREIGN KEY (provider_message_id, account_id)
                REFERENCES email_metadata(provider_message_id, account_id)
                ON DELETE CASCADE ON UPDATE CASCADE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_folder_members_folder "
        "ON email_folder_members (folder_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rules (
            rule_id                UUID         PRIMARY KEY,
            owner_user_id          UUID         NOT NULL
                                   REFERENCES users(user_id) ON DELETE CASCADE,
            name                   TEXT,
            is_enabled             BOOLEAN      NOT NULL DEFAULT TRUE,
            match_from_email       TEXT,
            match_subject_contains TEXT,
            target_folder_id       UUID         NOT NULL
                                   REFERENCES folders(folder_id) ON DELETE CASCADE,
            created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CHECK (match_from_email IS NOT NULL OR match_subject_contains IS NOT NULL)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rules_owner ON rules (owner_user_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_apply_jobs (
            rule_id         UUID         PRIMARY KEY
                            REFERENCES rules(rule_id) ON DELETE CASCADE,
            owner_user_id   UUID         NOT NULL,
            status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            page_cursor     TEXT,
            processed_count INTEGER      NOT NULL DEFAULT 0,
            attempts        INTEGER      NOT NULL DEFAULT 0,
            last_error      TEXT,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rule_apply_jobs_active "
        "ON rule_apply_jobs (status) "
        "WHERE status IN ('pending', 'running');"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rule_apply_jobs;")
    op.execute("DROP TABLE IF EXISTS rules;")
    op.execute("DROP TABLE IF EXISTS email_folder_members;")
    op.execute("DROP TABLE IF EXISTS folder_account_links;")
    op.execute("DROP TABLE IF EXISTS folders;")
