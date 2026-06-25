"""
Fallback migration runner used when Alembic is unavailable.
"""

from __future__ import annotations

from typing import Any

import psycopg2

from database.errors import DatabaseError, MigrationError


_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id       UUID         PRIMARY KEY,
        auth_provider VARCHAR(20)  NOT NULL CHECK (auth_provider IN ('google', 'microsoft')),
        provider_sub  VARCHAR(255) NOT NULL,
        email         VARCHAR(320) NOT NULL,
        name          VARCHAR(200),
        avatar_url    TEXT,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
        UNIQUE (auth_provider, provider_sub)
    );
    """,
    # Migration 0037: generalize a pre-0037 ``users`` table (single
    # ``google_sub UNIQUE NOT NULL``) to the multi-provider model. The whole
    # block is gated by the presence of ``google_sub`` so re-running the runner
    # over an already-migrated DB is a clean no-op: ``ALTER TABLE ... ADD
    # CONSTRAINT ... UNIQUE`` does NOT accept ``IF NOT EXISTS`` in PostgreSQL,
    # so a second run would otherwise crash with "constraint already exists".
    # On a fresh bootstrap the CREATE TABLE above already has the final shape
    # and ``google_sub`` never existed, so this is a no-op there too.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'google_sub'
        ) THEN
            ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20)
                NOT NULL DEFAULT 'google'
                CHECK (auth_provider IN ('google', 'microsoft'));
            ALTER TABLE users ADD COLUMN IF NOT EXISTS provider_sub VARCHAR(255);
            UPDATE users SET provider_sub = google_sub WHERE provider_sub IS NULL;
            ALTER TABLE users ALTER COLUMN provider_sub SET NOT NULL;
            ALTER TABLE users ALTER COLUMN auth_provider DROP DEFAULT;
            ALTER TABLE users DROP COLUMN google_sub;
            ALTER TABLE users ADD CONSTRAINT users_auth_provider_provider_sub_key
                UNIQUE (auth_provider, provider_sub);
        END IF;
    END
    $$;
    """,
    """
    CREATE TABLE IF NOT EXISTS mailboxes (
        mailbox_id    UUID         PRIMARY KEY,
        display_name  VARCHAR(120) NOT NULL,
        owner_user_id UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_mailboxes_owner_user_id ON mailboxes(owner_user_id);",
    """
    CREATE TABLE IF NOT EXISTS accounts (
        account_id              UUID         PRIMARY KEY,
        mailbox_id              UUID         NOT NULL
                                REFERENCES mailboxes(mailbox_id) ON DELETE CASCADE,
        provider                VARCHAR(20)  NOT NULL
                                CHECK (provider IN ('gmail', 'outlook')),
        display_label           VARCHAR(120) NOT NULL,
        config                  JSONB        NOT NULL DEFAULT '{}'::jsonb,
        created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
        access_token            TEXT,
        refresh_token           TEXT,
        access_token_encrypted  TEXT,
        refresh_token_encrypted TEXT,
        encryption_key_id       VARCHAR(64),
        expiry                  TIMESTAMPTZ,
        scopes                  TEXT[],
        tokens_updated_at       TIMESTAMPTZ
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_accounts_mailbox_id ON accounts(mailbox_id);",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id UUID        PRIMARY KEY,
        user_id    UUID        NOT NULL
                   REFERENCES users(user_id) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);",
    # Backward compatibility: these ALTERs are no-ops on fresh databases but ensure
    # databases created before migration 0003/0004 are brought up to date.
    "ALTER TABLE mailboxes ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(user_id) ON DELETE CASCADE;",
    "DELETE FROM mailboxes WHERE owner_user_id IS NULL;",
    "ALTER TABLE mailboxes ALTER COLUMN owner_user_id SET NOT NULL;",
    """
    CREATE TABLE IF NOT EXISTS alembic_version (
        version_num VARCHAR(64) PRIMARY KEY
    );
    """,
    # Migrations 0006–0007: sync_cursor + email_metadata + SENT box value
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_cursor TEXT;",
    """
    CREATE TABLE IF NOT EXISTS email_metadata (
        provider_message_id  VARCHAR(255) NOT NULL,
        account_id           UUID         NOT NULL
                             REFERENCES accounts(account_id) ON DELETE CASCADE,
        thread_id            VARCHAR(255),
        from_email           VARCHAR(320) NOT NULL,
        from_name            VARCHAR(200) DEFAULT '',
        subject              TEXT         NOT NULL DEFAULT '',
        received_at          TIMESTAMPTZ  NOT NULL,
        is_read              BOOLEAN      NOT NULL DEFAULT FALSE,
        box                  VARCHAR(20)  NOT NULL DEFAULT 'ALL_MAIL'
                             CHECK (box IN ('ALL_MAIL', 'SPAM', 'TRASH', 'SENT', 'DELETED', 'ARCHIVE')),
        previous_box         VARCHAR(20)  DEFAULT NULL
                             CHECK (previous_box IS NULL OR previous_box IN ('ALL_MAIL', 'SENT', 'SPAM', 'ARCHIVE')),
        PRIMARY KEY (provider_message_id, account_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_email_metadata_account_id ON email_metadata(account_id);",
    "CREATE INDEX IF NOT EXISTS idx_email_metadata_received_at ON email_metadata(received_at DESC);",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_address TEXT DEFAULT NULL;",
    # Migration 0010 seed (phantom user + sample emails) lives in
    # _SEED_STATEMENTS below, applied conditionally by _apply_statements when
    # DB_SEED_TEST_DATA is enabled. _DDL_STATEMENTS stays pure schema +
    # alembic_version stamps so production builds the schema without the seed.
    # Migration 0011 (+ 0013, 0021): email_content table with composite FK to
    # email_metadata. Fresh setups get the post-0021 shape directly so the
    # transitive cascade (accounts -> email_metadata -> email_content) is
    # wired in from day one, including ON UPDATE CASCADE so Outlook's
    # provider_message_id mutations propagate to cached content rows.
    """
    CREATE TABLE IF NOT EXISTS email_content (
        provider_message_id  VARCHAR(255) NOT NULL,
        account_id           UUID         NOT NULL,
        html_body            TEXT,
        text_body            TEXT,
        fetched_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
        last_accessed_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (provider_message_id, account_id),
        CONSTRAINT email_content_metadata_fkey
            FOREIGN KEY (provider_message_id, account_id)
            REFERENCES email_metadata(provider_message_id, account_id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    """,
    # Migration 0012 (+ 0022): drafts table for provider-first draft persistence.
    # Fresh setups get the post-0022 column name (``body``) directly so
    # repositories, queries, schemas and provider clients align with a single
    # name. Migration 0022 renames ``body_html`` -> ``body`` for already-
    # bootstrapped databases (handled below as an idempotent ALTER).
    """
    CREATE TABLE IF NOT EXISTS drafts (
        provider_draft_id VARCHAR(255) NOT NULL,
        account_id        UUID         NOT NULL
                          REFERENCES accounts(account_id) ON DELETE CASCADE,
        to_recipients     TEXT[]       NOT NULL DEFAULT '{}',
        cc_recipients     TEXT[]       NOT NULL DEFAULT '{}',
        bcc_recipients    TEXT[]       NOT NULL DEFAULT '{}',
        subject           TEXT         NOT NULL DEFAULT '',
        body              TEXT         NOT NULL DEFAULT '',
        created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (provider_draft_id, account_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_drafts_account_id ON drafts(account_id);",
    # Migration 0022: rename drafts.body_html -> drafts.body. No-op on fresh
    # databases (the inline CREATE TABLE above already uses the new name) but
    # required for databases bootstrapped before this migration ran.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'drafts' AND column_name = 'body_html'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'drafts' AND column_name = 'body'
        ) THEN
            ALTER TABLE drafts RENAME COLUMN body_html TO body;
        END IF;
    END
    $$;
    """,
    # One-shot cache invalidations covering migrations 0014–0019. Each of
    # those migrations truncated ``email_content`` to drop HTML cached under
    # an older rendering pipeline (CSS inlining, ``cid:``/``data:`` images,
    # MSO unwrap, charset normalisation, geometry mirroring, ``<style>``
    # preservation, head-only tag scrub, Outlook-hidden discard, etc.). For a
    # fresh bootstrap the table is empty, so a single TRUNCATE replaces all
    # of them — the per-migration TRUNCATE statements still live in the
    # individual Alembic files and run for incremental upgrades.
    "TRUNCATE TABLE email_content;",
    # Migration 0021: ensure email_content_metadata_fkey carries
    # ON UPDATE CASCADE on already-bootstrapped databases (the inline
    # CREATE TABLE above already covers fresh setups).
    "ALTER TABLE email_content DROP CONSTRAINT IF EXISTS email_content_metadata_fkey;",
    """
    ALTER TABLE email_content ADD CONSTRAINT email_content_metadata_fkey
        FOREIGN KEY (provider_message_id, account_id)
        REFERENCES email_metadata(provider_message_id, account_id)
        ON DELETE CASCADE ON UPDATE CASCADE;
    """,
    "DELETE FROM alembic_version;",
    "INSERT INTO alembic_version(version_num) VALUES ('0019_invalidate_email_content_cache_pipeline_refactor');",
    # Migration 0020: enable unaccent extension for accent-insensitive search
    "CREATE EXTENSION IF NOT EXISTS unaccent;",
    "UPDATE alembic_version SET version_num = '0020_create_extension_unaccent';",
    # Migration 0021: stamp only — the email_content_metadata_fkey ON UPDATE
    # CASCADE upgrade is applied above (and fresh setups already get the
    # final shape inline in the email_content CREATE TABLE).
    "UPDATE alembic_version SET version_num = '0021_email_content_fkey_on_update_cascade';",
    # Migration 0022: stamp only — the body_html → body rename is applied above
    # (and fresh setups already get the final shape inline in the drafts CREATE TABLE).
    "UPDATE alembic_version SET version_num = '0022_rename_drafts_body_html_to_body';",
    # Migration 0023: attachments tables and the has_attachments flag.
    # ``email_attachments`` carries per-attachment metadata; ``email_attachment_blobs``
    # holds the binary in BYTEA so SELECT * over metadata never accidentally pulls
    # megabytes; ``draft_attachments`` holds composer-stage attachments locally
    # until send/save (D-07 lazy push). The two partial unique indexes encode
    # the asymmetry between providers (Gmail keys by part_id, Outlook by
    # provider_attachment_id under ImmutableId).
    """
    CREATE TABLE IF NOT EXISTS email_attachments (
        attachment_id            UUID PRIMARY KEY,
        account_id               UUID NOT NULL,
        provider_message_id      VARCHAR(255) NOT NULL,
        part_id                  TEXT NULL,
        provider_attachment_id   TEXT NULL,
        filename                 TEXT NOT NULL,
        mime_type                TEXT NOT NULL,
        size                     BIGINT NOT NULL,
        content_id               TEXT NULL,
        is_inline                BOOLEAN NOT NULL DEFAULT FALSE,
        position                 INT NOT NULL DEFAULT 0,
        created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_accessed_at         TIMESTAMPTZ NULL,
        unavailable_at           TIMESTAMPTZ NULL,
        CONSTRAINT email_attachments_metadata_fkey
            FOREIGN KEY (account_id, provider_message_id)
            REFERENCES email_metadata (account_id, provider_message_id)
            ON DELETE CASCADE
            ON UPDATE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_email_attachments_message ON email_attachments (account_id, provider_message_id);",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_email_attachments_gmail
        ON email_attachments (account_id, provider_message_id, part_id)
        WHERE part_id IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_email_attachments_outlook
        ON email_attachments (account_id, provider_message_id, provider_attachment_id)
        WHERE provider_attachment_id IS NOT NULL AND part_id IS NULL;
    """,
    """
    CREATE TABLE IF NOT EXISTS email_attachment_blobs (
        attachment_id        UUID PRIMARY KEY,
        blob                 BYTEA NULL,
        blob_storage_kind    TEXT NOT NULL DEFAULT 'db',
        blob_ref             TEXT NULL,
        fetched_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT email_attachment_blobs_attachment_fkey
            FOREIGN KEY (attachment_id)
            REFERENCES email_attachments (attachment_id)
            ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS draft_attachments (
        draft_attachment_id     UUID PRIMARY KEY,
        account_id              UUID NOT NULL,
        provider_draft_id       VARCHAR(255) NOT NULL,
        filename                TEXT NOT NULL,
        mime_type               TEXT NOT NULL,
        size                    BIGINT NOT NULL,
        content_id              TEXT NULL,
        is_inline               BOOLEAN NOT NULL DEFAULT FALSE,
        position                INT NOT NULL DEFAULT 0,
        blob                    BYTEA NULL,
        blob_storage_kind       TEXT NOT NULL DEFAULT 'db',
        blob_ref                TEXT NULL,
        provider_attachment_id  TEXT NULL,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT draft_attachments_draft_fkey
            FOREIGN KEY (account_id, provider_draft_id)
            REFERENCES drafts (account_id, provider_draft_id)
            ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_draft_attachments_draft ON draft_attachments (account_id, provider_draft_id);",
    "ALTER TABLE email_metadata ADD COLUMN IF NOT EXISTS has_attachments BOOLEAN NOT NULL DEFAULT FALSE;",
    "UPDATE alembic_version SET version_num = '0023_create_attachments_tables';",
    # Migration 0024: invalidate email_content cache after the strict
    # inline-vs-attachment split. Fresh bootstraps already have an empty
    # email_content (TRUNCATE above), so this is a no-op for greenfield
    # databases; for incremental upgrades the per-migration TRUNCATE in
    # 0024 still runs via Alembic.
    "TRUNCATE TABLE email_content;",
    "UPDATE alembic_version SET version_num = '0024_invalidate_email_content_cache_attachments_split';",
    # Migration 0025: composite index backing the drafts listing queries
    # (filter by account_id, order by created_at DESC). Idempotent — fresh
    # setups create it; pre-existing databases get it via the Alembic file.
    "CREATE INDEX IF NOT EXISTS ix_drafts_account_created ON drafts (account_id, created_at DESC);",
    "UPDATE alembic_version SET version_num = '0025_index_drafts_account_created';",
    # Migration 0026: partial index backing the admin TTL purge over
    # ``email_attachments.last_accessed_at``. Pure DDL, no cache invalidation.
    "CREATE INDEX IF NOT EXISTS idx_email_attachments_last_accessed "
    "ON email_attachments (last_accessed_at) "
    "WHERE last_accessed_at IS NOT NULL;",
    "UPDATE alembic_version SET version_num = '0026_index_email_attachments_last_accessed';",
    # Migration 0027: favourites — ``is_favorite`` denormalised on
    # ``email_metadata`` as the cross-provider abstraction over Gmail's
    # ``STARRED`` label and Outlook's message flag. Pure DDL plus a
    # partial index over the favourites-only subset.
    "ALTER TABLE email_metadata ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT FALSE;",
    "CREATE INDEX IF NOT EXISTS idx_email_metadata_favorites "
    "ON email_metadata (account_id, received_at DESC) "
    "WHERE is_favorite = TRUE;",
    "UPDATE alembic_version SET version_num = '0027_add_is_favorite_to_email_metadata';",
    # Migration 0028 (+ 0032): virtual_mailboxes — user-defined filtered
    # views over email_metadata. The current shape is a flat
    # ``scope_payload = {"account_ids":[...]}`` JSONB + ``filter_payload``;
    # ``scope_kind`` was dropped by migration 0032 (the indirection
    # collapsed once the three "scope_kind" branches stopped offering
    # value over an explicit account list).
    """
    CREATE TABLE IF NOT EXISTS virtual_mailboxes (
        virtual_mailbox_id  UUID         PRIMARY KEY,
        owner_user_id       UUID         NOT NULL
                             REFERENCES users(user_id) ON DELETE CASCADE,
        display_name        VARCHAR(120) NOT NULL,
        scope_payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
        filter_payload      JSONB        NOT NULL DEFAULT '{}'::jsonb,
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_virtual_mailboxes_owner ON virtual_mailboxes(owner_user_id);",
    "UPDATE alembic_version SET version_num = '0028_create_virtual_mailboxes_table';",
    # Migration 0029: reply / forward threading columns on ``drafts``. All
    # nullable — pre-existing drafts and "compose from scratch" rows keep
    # them NULL. The partial index supports future "list drafts that reply
    # to <message>" lookups without bloating the index with NULL rows.
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS reply_kind VARCHAR(20) "
    "CHECK (reply_kind IN ('reply', 'reply_all', 'forward'));",
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS reply_to_message_id VARCHAR(255);",
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS reply_to_account_id UUID;",
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS thread_id VARCHAR(255);",
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS in_reply_to VARCHAR(998);",
    "ALTER TABLE drafts ADD COLUMN IF NOT EXISTS references_header TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_drafts_reply_to_message_id "
    "ON drafts (reply_to_account_id, reply_to_message_id) "
    "WHERE reply_to_message_id IS NOT NULL;",
    "UPDATE alembic_version SET version_num = '0029_drafts_reply_threading';",
    # Migration 0030: source-tracking columns on ``draft_attachments`` for
    # Forward copies. The partial index keys the idempotency check used by
    # ``copy_attachments_from_email`` (R-12) — only Forward-copied rows pay.
    "ALTER TABLE draft_attachments ADD COLUMN IF NOT EXISTS source_account_id UUID;",
    "ALTER TABLE draft_attachments ADD COLUMN IF NOT EXISTS source_attachment_id UUID;",
    "CREATE INDEX IF NOT EXISTS idx_draft_attachments_source "
    "ON draft_attachments (account_id, provider_draft_id, source_attachment_id) "
    "WHERE source_attachment_id IS NOT NULL;",
    "UPDATE alembic_version SET version_num = '0030_draft_attachments_source';",
    # Migration 0031: capture the first ``To`` recipient on ``email_metadata``
    # (symmetric with the existing ``from_email`` / ``from_name`` pair) so the
    # sent / unified-sent inbox can render the destination without an extra
    # provider round trip. Nullable defaults so existing rows survive the ALTER.
    "ALTER TABLE email_metadata ADD COLUMN IF NOT EXISTS to_email VARCHAR(320) NOT NULL DEFAULT '';",
    "ALTER TABLE email_metadata ADD COLUMN IF NOT EXISTS to_name VARCHAR(200) NOT NULL DEFAULT '';",
    "UPDATE alembic_version SET version_num = '0031_add_to_email_to_email_metadata';",
    # Migration 0032: collapse scope_kind / scope_payload into a flat
    # ``scope_payload = {"account_ids":[...]}``. Fresh bootstraps already
    # get the post-0032 CREATE TABLE shape above (no ``scope_kind``
    # column), so the DO block below is a no-op for greenfield databases
    # and applies the snapshot + DROP only on incremental upgrades that
    # still carry the old column.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'virtual_mailboxes' AND column_name = 'scope_kind'
        ) THEN
            UPDATE virtual_mailboxes vmb
            SET scope_payload = jsonb_build_object(
                'account_ids',
                COALESCE(
                    (
                        SELECT jsonb_agg(a.account_id::text)
                        FROM mailboxes m
                        JOIN accounts a ON a.mailbox_id = m.mailbox_id
                        WHERE m.owner_user_id = vmb.owner_user_id
                    ),
                    '[]'::jsonb
                )
            )
            WHERE scope_kind = 'all';

            UPDATE virtual_mailboxes vmb
            SET scope_payload = jsonb_build_object(
                'account_ids',
                COALESCE(
                    (
                        SELECT jsonb_agg(a.account_id::text)
                        FROM accounts a
                        JOIN mailboxes m ON m.mailbox_id = a.mailbox_id
                        WHERE m.mailbox_id::text = vmb.scope_payload->>'mailbox_id'
                          AND m.owner_user_id = vmb.owner_user_id
                    ),
                    '[]'::jsonb
                )
            )
            WHERE scope_kind = 'mailbox';

            UPDATE virtual_mailboxes
            SET scope_payload = jsonb_build_object(
                'account_ids',
                COALESCE(scope_payload->'account_ids', '[]'::jsonb)
            );

            ALTER TABLE virtual_mailboxes
                DROP CONSTRAINT IF EXISTS virtual_mailboxes_scope_kind_check;
            ALTER TABLE virtual_mailboxes DROP COLUMN scope_kind;
        END IF;
    END
    $$;
    """,
    "UPDATE alembic_version SET version_num = '0032_drop_virtual_mailbox_scope_kind';",
    # Migration 0033: composite index backing the conversation grouping
    # listing (DISTINCT ON / window functions over the thread key + order by
    # received_at). Pure DDL, idempotent, no cache invalidation — the grouped
    # queries are correct without it (it only avoids seq-scan + sort).
    "CREATE INDEX IF NOT EXISTS idx_email_metadata_account_thread "
    "ON email_metadata (account_id, thread_id, received_at DESC);",
    "UPDATE alembic_version SET version_num = '0033_index_email_metadata_thread';",
    # Migration 0034: convert every plain-text ``drafts.body`` to HTML for
    # the rich-text composer (no ``body_format`` discriminator — body is
    # always HTML from here on). Mirrors the Python conversion in the
    # Alembic file with equivalent SQL: HTML-escape ``&`` / ``<`` / ``>``
    # FIRST (order matters — ``&`` must be escaped before ``<``/``>`` so
    # their ``&lt;``/``&gt;`` are not re-escaped), THEN turn CR/LF into
    # ``<br>`` and wrap in a single ``<p>…</p>``. Idempotent: only rows
    # with a non-blank body that does NOT already look like HTML (no
    # leading ``<p`` / no ``<br``) are touched, so a re-run never
    # double-escapes. Empty / whitespace-only bodies stay ``''``.
    r"""
    UPDATE drafts
    SET body = '<p>' || replace(
                            replace(
                                replace(
                                    replace(
                                        replace(
                                            replace(body, '&', '&amp;'),
                                            '<', '&lt;'),
                                        '>', '&gt;'),
                                    E'\r\n', E'\n'),
                                E'\r', E'\n'),
                            E'\n', '<br>') || '</p>'
    WHERE btrim(body) <> ''
      AND lower(left(btrim(body), 64)) NOT LIKE '<p%'
      AND lower(left(btrim(body), 64)) NOT LIKE '%<br%';
    """,
    "UPDATE alembic_version SET version_num = '0034_convert_drafts_body_to_html';",
    # Migration 0035: invalidate email_content cache after allow-listing the
    # legacy ``background`` attribute (table/cell background-image carrier).
    # HTML cached before this change had the attribute stripped, so product
    # grids that paint the thumbnail via ``<td background="https://…">`` (e.g.
    # AliExpress EDM) rendered as grey boxes. TRUNCATE forces a cache-aside
    # refetch + re-sanitise on next view. No-op on a greenfield bootstrap
    # (the table is already empty from the TRUNCATE above); for incremental
    # upgrades the per-migration TRUNCATE in 0035 still runs via Alembic.
    "TRUNCATE TABLE email_content;",
    "UPDATE alembic_version SET version_num = '0035_invalidate_email_content_cache_background_attr';",
    # Migration 0036: sliding-TTL eviction for email_content. The column is
    # also declared in the CREATE TABLE above for fresh bootstraps; the ALTER
    # covers incremental upgrades of an existing table. The composite index
    # (account_id, last_accessed_at) backs the per-account purge
    # (PURGE_EXPIRED_FOR_ACCOUNTS). The TRUNCATE is a one-shot invalidation
    # after unifying the body+attachments provider read — no-op on a fresh
    # bootstrap (the table is already empty above).
    "ALTER TABLE email_content ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now();",
    "CREATE INDEX IF NOT EXISTS idx_email_content_last_accessed ON email_content (account_id, last_accessed_at);",
    "TRUNCATE TABLE email_content;",
    "UPDATE alembic_version SET version_num = '0036_email_content_ttl_and_truncate';",
    # Migration 0037: generalize users to (auth_provider, provider_sub). The
    # schema change itself is applied above (fresh CREATE TABLE shape + the
    # gated DO-block upgrade for pre-existing databases); this is the stamp only.
    "UPDATE alembic_version SET version_num = '0037_generalize_users_auth_provider';",
    # Migration 0038: add ARCHIVE to the box and previous_box CHECK constraints.
    # The CREATE TABLE above already carries the final form for fresh bootstraps;
    # these DROP/ADD cover incremental upgrades of an existing table. Both
    # constraints must move: box gains the new 'ARCHIVE' location, and
    # previous_box gains it so trashing an archived message (MOVE_TO_TRASH_BATCH
    # copies box → previous_box) is not rejected.
    "ALTER TABLE email_metadata DROP CONSTRAINT IF EXISTS email_metadata_box_check;",
    "ALTER TABLE email_metadata ADD CONSTRAINT email_metadata_box_check "
    "CHECK (box IN ('ALL_MAIL', 'SPAM', 'TRASH', 'SENT', 'DELETED', 'ARCHIVE'));",
    "ALTER TABLE email_metadata DROP CONSTRAINT IF EXISTS email_metadata_previous_box_check;",
    "ALTER TABLE email_metadata ADD CONSTRAINT email_metadata_previous_box_check "
    "CHECK (previous_box IS NULL OR previous_box IN ('ALL_MAIL', 'SENT', 'SPAM', 'ARCHIVE'));",
    "UPDATE alembic_version SET version_num = '0038_add_archive_box_value';",
    # Migration 0039: signature_html column on accounts (per-account email
    # signature, local-only). Pure ADD COLUMN, nullable, no cache invalidation.
    # Renumbered from 0038 → 0039 because migration 0038 was taken by the
    # archive-box change above (both features landed independently); chained
    # after it to keep the runner stamp order aligned with the Alembic chain.
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS signature_html TEXT DEFAULT NULL;",
    "UPDATE alembic_version SET version_num = '0039_add_signature_html_to_accounts';",
]


# Migration 0010 seed data — phantom user + ~100 sample emails for GET endpoint
# tests. Applied by _apply_statements only when DB_SEED_TEST_DATA is enabled
# (default True; production sets it false). Kept in lockstep with the gated
# upgrade() of the Alembic migration 0010_seed_fake_data_for_get_tests.
_SEED_STATEMENTS = [
    """
    INSERT INTO users (user_id, auth_provider, provider_sub, email, name)
    VALUES (
        '11111111-1111-4000-a000-111111111111',
        'google',
        'inventadoParaEndpointGet-google-sub',
        'inventadoParaEndpointGet@fake.test',
        'inventadoParaEndpointGet'
    )
    ON CONFLICT (auth_provider, provider_sub) DO NOTHING;
    """,
    """
    INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id)
    VALUES
        ('aaaaaaaa-aaaa-4000-a000-aaaaaaaaa001', 'Gmail inventada',   '11111111-1111-4000-a000-111111111111'),
        ('aaaaaaaa-aaaa-4000-a000-aaaaaaaaa002', 'Outlook inventada', '11111111-1111-4000-a000-111111111111')
    ON CONFLICT (mailbox_id) DO NOTHING;
    """,
    """
    INSERT INTO accounts (account_id, mailbox_id, provider, display_label, email_address)
    VALUES
        ('bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'aaaaaaaa-aaaa-4000-a000-aaaaaaaaa001', 'gmail',   'Gmail inventada - inventadoParaEndpointGet',   'gmailinventada@gmail.com'),
        ('bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'aaaaaaaa-aaaa-4000-a000-aaaaaaaaa002', 'outlook', 'Outlook inventada - inventadoParaEndpointGet', 'outlookinventada@outlook.com')
    ON CONFLICT (account_id) DO NOTHING;
    """,
    """
    INSERT INTO email_metadata (provider_message_id, account_id, thread_id, from_email, from_name, subject, received_at, is_read, box, previous_box)
    VALUES
        ('gmail-allmail-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-001', 'alice@example.com',    'Alice Johnson',    'Reunión de proyecto mañana',              '2026-03-01T09:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-001', 'bob@example.com',      'Bob Martinez',     'Re: Reunión de proyecto mañana',           '2026-03-01T09:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-002', 'carol@startup.io',     'Carol Chen',       'Propuesta de colaboración',                '2026-03-02T08:15:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-003', 'dave@corp.com',        'Dave Wilson',      'Actualización del presupuesto Q1',         '2026-03-02T14:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-003', 'eve@corp.com',         'Eve Thompson',     'Re: Actualización del presupuesto Q1',     '2026-03-02T15:20:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-004', 'frank@university.edu', 'Frank Rivera',     'Material del curso actualizado',           '2026-03-03T07:45:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-007', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-005', 'grace@design.co',      'Grace Kim',        'Revisión de mockups v3',                   '2026-03-03T11:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-008', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-005', 'henry@design.co',      'Henry Park',       'Re: Revisión de mockups v3',               '2026-03-03T11:45:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-009', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-006', 'irene@legal.com',      'Irene Salazar',    'Contrato pendiente de firma',              '2026-03-04T08:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-010', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-007', 'jack@devops.net',      'Jack Turner',      'Alerta: CPU al 95% en producción',         '2026-03-04T10:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-011', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-007', 'jack@devops.net',      'Jack Turner',      'Re: Alerta resuelta - CPU normalizada',    '2026-03-04T12:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-012', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-008', 'karen@hr.com',         'Karen López',      'Recordatorio: evaluación de desempeño',    '2026-03-05T09:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-013', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-009', 'leo@finance.org',      'Leo Nakamura',     'Factura #4521 adjunta',                    '2026-03-05T13:15:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-014', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-010', 'mia@marketing.com',    'Mia Santos',       'Campaña de lanzamiento - borrador',        '2026-03-06T08:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-015', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-010', 'noah@marketing.com',   'Noah Gupta',       'Re: Campaña de lanzamiento - aprobado',    '2026-03-06T10:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-016', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-011', 'olivia@support.io',    'Olivia Brown',     'Ticket #8832 - Error en dashboard',        '2026-03-07T07:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-017', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-012', 'paul@research.edu',    'Paul Anderson',    'Resultados del estudio preliminar',        '2026-03-07T14:45:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-018', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-013', 'quinn@sales.com',      'Quinn Roberts',    'Nuevo cliente potencial - Tech Solutions', '2026-03-08T09:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-019', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-014', 'rachel@pm.com',        'Rachel Davis',     'Sprint planning - semana 12',              '2026-03-08T15:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-020', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-014', 'sam@pm.com',           'Sam Mitchell',     'Re: Sprint planning - tareas asignadas',   '2026-03-09T08:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-021', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-015', 'tina@analytics.com',   'Tina Fernández',   'Informe mensual de métricas',              '2026-03-09T11:20:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-022', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-016', 'ulises@infra.net',     'Ulises Vega',      'Migración a Kubernetes programada',        '2026-03-10T07:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-023', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-017', 'vera@qa.com',          'Vera White',       'Regresión detectada en módulo de pagos',   '2026-03-10T13:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-024', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-017', 'will@qa.com',          'Will Scott',       'Re: Regresión corregida - hotfix v2.1.3',  '2026-03-10T16:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-025', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-018', 'xena@partners.com',    'Xena Morales',     'Acuerdo de partnership - revisión legal',  '2026-03-11T08:45:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-026', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-019', 'yuri@backend.dev',     'Yuri Tanaka',      'PR #342 lista para review',                '2026-03-11T14:10:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-027', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-020', 'zoe@ux.design',        'Zoe Ellis',        'Prototipo interactivo compartido',         '2026-03-12T09:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-allmail-028', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-021', 'adam@security.io',     'Adam Blake',       'Auditoría de seguridad completada',        '2026-03-12T15:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-029', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-022', 'bella@data.org',       'Bella Chang',      'Dataset actualizado en S3',                '2026-03-13T08:20:00Z', TRUE,  'ALL_MAIL', NULL),
        ('gmail-allmail-030', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-023', 'carlos@mobile.dev',    'Carlos Herrera',   'Build de iOS fallido - investigando',      '2026-03-13T12:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('gmail-sent-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-001', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Reunión de proyecto mañana - confirmado',    '2026-03-01T10:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-003', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Presupuesto Q1 - aprobado',                  '2026-03-02T16:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-006', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Contrato - firmado y adjunto',               '2026-03-04T09:30:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-024', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Solicitud de acceso a repositorio',              '2026-03-05T10:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-025', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Invitación a demo del producto',                 '2026-03-06T11:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-011', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Ticket #8832 - más detalles adjuntos',       '2026-03-07T08:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-007', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-026', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Propuesta técnica para migración',               '2026-03-08T14:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-008', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-015', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Métricas - solicitud de desglose',           '2026-03-09T12:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-009', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-019', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: PR #342 - cambios solicitados',              '2026-03-11T15:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-sent-010', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-022', 'gmailinventada@gmail.com', 'inventadoParaEndpointGet', 'Re: Dataset - consulta sobre formato',           '2026-03-13T09:00:00Z', TRUE, 'SENT', NULL),
        ('gmail-trash-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-027', 'spam-legit@promos.com',     'Promos Weekly',          'Oferta exclusiva solo hoy',                    '2026-03-02T06:00:00Z', TRUE,  'TRASH', 'ALL_MAIL'),
        ('gmail-trash-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-028', 'newsletter@oldservice.com', 'Old Service Newsletter', 'Tu resumen semanal - semana 9',                '2026-03-04T06:30:00Z', FALSE, 'TRASH', 'ALL_MAIL'),
        ('gmail-trash-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-029', 'noreply@social.com',        'Social Network',         'Alguien te mencionó en un comentario',         '2026-03-06T07:00:00Z', TRUE,  'TRASH', 'ALL_MAIL'),
        ('gmail-trash-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-030', 'alerts@shopping.com',       'Shopping Alerts',        'Precio rebajado en tu lista de deseos',        '2026-03-09T06:45:00Z', FALSE, 'TRASH', 'ALL_MAIL'),
        ('gmail-spam-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-031', 'winner@lottery-fake.com', 'Lottery Winner',        'Has ganado 1,000,000 USD',                     '2026-03-01T05:00:00Z', FALSE, 'SPAM', NULL),
        ('gmail-spam-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-032', 'prince@scam.ng',          'Nigerian Prince',       'Urgent business proposal',                     '2026-03-03T04:30:00Z', FALSE, 'SPAM', NULL),
        ('gmail-spam-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-033', 'free@crypto-scam.xyz',    'Free Crypto',           'Claim your free Bitcoin now',                  '2026-03-05T03:00:00Z', FALSE, 'SPAM', NULL),
        ('gmail-spam-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-034', 'admin@phishing-bank.com', 'Your Bank Security',    'Verify your account immediately',              '2026-03-07T02:15:00Z', FALSE, 'SPAM', NULL),
        ('gmail-spam-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-035', 'deals@cheap-meds.ru',     'Pharmacy Deals',        '70 pct off all medications - limited time',    '2026-03-09T01:00:00Z', FALSE, 'SPAM', NULL),
        ('gmail-spam-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb001', 'thread-gm-036', 'support@fake-apple.com',  'Apple Support (fake)',   'Your Apple ID has been compromised',           '2026-03-11T00:30:00Z', FALSE, 'SPAM', NULL)
    ON CONFLICT (provider_message_id, account_id) DO NOTHING;
    """,
    """
    INSERT INTO email_metadata (provider_message_id, account_id, thread_id, from_email, from_name, subject, received_at, is_read, box, previous_box)
    VALUES
        ('outlook-allmail-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-001', 'diana@corporate.com',     'Diana Foster',      'Agenda del comité directivo',                '2026-03-01T08:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-001', 'ethan@corporate.com',     'Ethan Hayes',       'Re: Agenda del comité directivo',            '2026-03-01T08:45:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-002', 'fiona@consulting.com',    'Fiona Grant',       'Entrega del informe de auditoría',           '2026-03-02T09:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-003', 'george@logistics.com',    'George Patel',      'Retraso en envío lote #7890',                '2026-03-02T13:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-003', 'hannah@logistics.com',    'Hannah Reed',       'Re: Retraso resuelto - envío reprogramado',  '2026-03-02T17:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-004', 'ivan@training.com',       'Ivan Kozlov',       'Certificación AWS - fecha de examen',        '2026-03-03T08:15:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-007', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-005', 'julia@product.com',       'Julia Mason',       'Roadmap Q2 - borrador para revisión',        '2026-03-03T11:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-008', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-005', 'kevin@product.com',       'Kevin Walsh',       'Re: Roadmap Q2 - comentarios añadidos',      '2026-03-03T14:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-009', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-006', 'laura@compliance.com',    'Laura Bennett',     'GDPR - actualización de política requerida', '2026-03-04T07:45:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-010', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-007', 'marcus@engineering.com',  'Marcus Young',      'Incidencia en API gateway - postmortem',     '2026-03-04T12:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-011', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-007', 'nina@engineering.com',    'Nina Ortiz',        'Re: Postmortem - action items asignados',    '2026-03-04T15:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-012', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-008', 'oscar@procurement.com',   'Oscar Rivera',      'Orden de compra #12345 aprobada',            '2026-03-05T09:15:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-013', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-009', 'patricia@events.com',     'Patricia Hughes',   'Conferencia Tech Summit - confirmación',     '2026-03-05T14:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-014', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-010', 'roberto@platform.io',     'Roberto Duarte',    'Release v3.5.0 - notas de versión',          '2026-03-06T08:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-015', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-010', 'sandra@platform.io',      'Sandra Kim',        'Re: Release v3.5.0 - deploy exitoso',        '2026-03-06T11:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-016', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-011', 'tomas@architecture.com',  'Tomás Vargas',      'RFC: migración a microservicios',            '2026-03-07T07:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-017', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-012', 'ursula@testing.com',      'Ursula Weber',      'Cobertura de tests al 92 pct - informe',     '2026-03-07T15:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-018', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-013', 'victor@cloud.com',        'Victor Nilsson',    'Factura AWS marzo - 4230 USD',               '2026-03-08T09:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-019', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-014', 'wendy@design.com',        'Wendy Torres',      'Design system v2 - componentes listos',      '2026-03-08T14:45:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-020', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-014', 'xavier@design.com',       'Xavier Luna',       'Re: Design system v2 - feedback',            '2026-03-09T08:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-021', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-015', 'yolanda@support.com',     'Yolanda Brooks',    'Escalación cliente VIP - prioridad alta',    '2026-03-09T11:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-022', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-016', 'zach@devrel.com',         'Zach Cooper',       'Hackathon interno - inscripciones abiertas', '2026-03-10T07:15:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-023', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-017', 'alicia@strategy.com',     'Alicia Morgan',     'Análisis competitivo Q1 - presentación',     '2026-03-10T13:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-024', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-017', 'bruno@strategy.com',      'Bruno Castillo',    'Re: Análisis competitivo - datos extra',     '2026-03-10T16:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-025', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-018', 'cecilia@onboarding.com',  'Cecilia Adams',     'Nuevo empleado - setup de accesos',          '2026-03-11T08:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-026', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-019', 'derek@database.com',      'Derek O Brien',     'Optimización de queries - resultados',       '2026-03-11T14:30:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-027', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-020', 'elena@frontend.dev',      'Elena Sato',        'Lighthouse score mejorado a 98',             '2026-03-12T08:45:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-028', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-021', 'fabian@monitoring.io',    'Fabián Cruz',       'Alerta: latencia alta en endpoint /api/v2',  '2026-03-12T15:00:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-allmail-029', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-022', 'gabriela@legal.com',      'Gabriela Stone',    'NDA firmado - copia adjunta',                '2026-03-13T08:00:00Z', TRUE,  'ALL_MAIL', NULL),
        ('outlook-allmail-030', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-023', 'hector@cicd.io',          'Héctor Romero',     'Pipeline CI roto en rama develop',           '2026-03-13T12:30:00Z', FALSE, 'ALL_MAIL', NULL),
        ('outlook-sent-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-001', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: Agenda del comité - puntos añadidos',        '2026-03-01T09:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-003', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: Envío - solicitud de tracking',              '2026-03-02T18:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-006', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: GDPR - política actualizada adjunta',        '2026-03-04T09:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-024', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Solicitud de presupuesto para herramientas',     '2026-03-05T10:30:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-025', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Reporte semanal de progreso - semana 10',        '2026-03-06T12:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-011', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: RFC microservicios - comentarios',           '2026-03-07T09:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-007', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-026', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Documentación de API actualizada',               '2026-03-08T15:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-008', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-015', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: Escalación VIP - seguimiento realizado',     '2026-03-09T12:30:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-009', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-019', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: Queries optimizadas - aprobado para prod',   '2026-03-11T15:30:00Z', TRUE, 'SENT', NULL),
        ('outlook-sent-010', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-022', 'outlookinventada@outlook.com', 'inventadoParaEndpointGet', 'Re: NDA - confirmación de recepción',            '2026-03-13T09:00:00Z', TRUE, 'SENT', NULL),
        ('outlook-trash-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-027', 'events@webinar-spam.com',  'Webinar Invites',        'Webinar gratuito: cómo ganar dinero rápido',   '2026-03-02T05:30:00Z', TRUE,  'TRASH', 'ALL_MAIL'),
        ('outlook-trash-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-028', 'digest@oldplatform.com',   'Old Platform Digest',    'Tu actividad de la semana en OldPlatform',     '2026-03-04T06:00:00Z', FALSE, 'TRASH', 'ALL_MAIL'),
        ('outlook-trash-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-029', 'noreply@forum.old',        'Old Forum',              'Nuevo post en hilo que sigues',                '2026-03-06T07:30:00Z', TRUE,  'TRASH', 'ALL_MAIL'),
        ('outlook-trash-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-030', 'promo@deals-daily.com',    'Daily Deals',            'Flash sale - últimas 2 horas',                 '2026-03-09T06:00:00Z', FALSE, 'TRASH', 'ALL_MAIL'),
        ('outlook-spam-001', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-031', 'ceo@fake-company.biz',     'Fake CEO',                  'Wire transfer needed urgently',                '2026-03-01T04:00:00Z', FALSE, 'SPAM', NULL),
        ('outlook-spam-002', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-032', 'helpdesk@phish-it.com',    'IT Helpdesk (fake)',         'Password reset required - click here',         '2026-03-03T03:30:00Z', FALSE, 'SPAM', NULL),
        ('outlook-spam-003', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-033', 'invest@ponzi-scheme.xyz',  'Investment Guru',            '300 pct returns guaranteed - act now',          '2026-03-05T02:00:00Z', FALSE, 'SPAM', NULL),
        ('outlook-spam-004', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-034', 'survey@gift-card-scam.com','Free Gift Cards',            'Complete survey for 500 USD Amazon gift card',  '2026-03-07T01:15:00Z', FALSE, 'SPAM', NULL),
        ('outlook-spam-005', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-035', 'pills@miracle-health.ru',  'Miracle Health',             'Lose 20kg in 1 week - doctors hate this',      '2026-03-09T00:45:00Z', FALSE, 'SPAM', NULL),
        ('outlook-spam-006', 'bbbbbbbb-bbbb-4000-a000-bbbbbbbbb002', 'thread-ol-036', 'microsoft@fake-ms.com',    'Microsoft Security (fake)',  'Your Office 365 license will expire today',    '2026-03-11T00:00:00Z', FALSE, 'SPAM', NULL)
    ON CONFLICT (provider_message_id, account_id) DO NOTHING;
    """,
]


def _apply_statements(cur: Any) -> None:
    # Deferred import: the flag must be read at apply time, not at module import
    # (the environment may not be loaded yet when this module is imported), and
    # settings is the only module allowed to read os.environ.
    from database.settings import is_test_data_seed_enabled

    for statement in _DDL_STATEMENTS:
        cur.execute(statement)
    if is_test_data_seed_enabled():
        for statement in _SEED_STATEMENTS:
            cur.execute(statement)


def ensure_schema_at_head(dsn: str) -> None:
    """
    Apply idempotent head schema without Alembic runtime dependency.
    """
    try:
        with psycopg2.connect(dsn=dsn) as conn:
            with conn.cursor() as cur:
                _apply_statements(cur)
    except psycopg2.Error as exc:
        raise MigrationError(
            "Failed to apply fallback database migrations."
        ) from exc
    except DatabaseError:
        # A DatabaseError (e.g. SettingsError from an invalid DB_SEED_TEST_DATA
        # read inside _apply_statements) must surface as-is; re-wrapping it as
        # MigrationError would double-wrap and mask the fail-fast signal
        # (database/CLAUDE.md §7: never double-wrap DatabaseError).
        raise
    except Exception as exc:
        raise MigrationError(
            f"Unexpected migration error ({type(exc).__name__}): {exc}"
        ) from exc
