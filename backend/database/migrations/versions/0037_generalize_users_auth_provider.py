"""
Generalize the ``users`` identity model from Google-only to multi-provider.

Replaces the single ``google_sub UNIQUE NOT NULL`` column with the pair
``auth_provider`` (``'google'`` | ``'microsoft'``) + ``provider_sub`` and a
composite unique key ``(auth_provider, provider_sub)``. This is what lets a
Microsoft OIDC login coexist with the existing Google login: each provider
identifies its own user by the provider's stable ``sub`` claim, scoped by
``auth_provider`` (no account linking — same email on two providers means two
distinct users).

Backfill order is load-bearing: ``auth_provider`` is added with a temporary
``DEFAULT 'google'`` so every pre-existing row (all Google logins) is stamped
correctly, ``provider_sub`` is backfilled from ``google_sub`` BEFORE the old
column is dropped, and only then is the temporary default removed so future
inserts must pass ``auth_provider`` explicitly. Dropping ``google_sub`` drags
its implicit ``users_google_sub_key`` unique constraint with it.
"""

from __future__ import annotations

from alembic import op


revision = "0037_generalize_users_auth_provider"
down_revision = "0036_email_content_ttl_and_truncate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. auth_provider with a temporary DEFAULT 'google' to backfill existing rows.
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) "
        "NOT NULL DEFAULT 'google' "
        "CHECK (auth_provider IN ('google', 'microsoft'));"
    )
    # 2. provider_sub nullable, then backfilled from google_sub.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS provider_sub VARCHAR(255);")
    op.execute("UPDATE users SET provider_sub = google_sub WHERE provider_sub IS NULL;")
    # 3. provider_sub becomes NOT NULL once every row carries a value.
    op.execute("ALTER TABLE users ALTER COLUMN provider_sub SET NOT NULL;")
    # 4. Drop the temporary default: auth_provider is explicit on every insert now.
    op.execute("ALTER TABLE users ALTER COLUMN auth_provider DROP DEFAULT;")
    # 5. Drop google_sub (drags its implicit users_google_sub_key unique with it).
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_sub;")
    # 6. New composite unique key. ADD CONSTRAINT ... UNIQUE does not accept
    #    IF NOT EXISTS in PostgreSQL; Alembic runs this exactly once (it seals
    #    alembic_version), so no idempotency guard is needed here.
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_auth_provider_provider_sub_key "
        "UNIQUE (auth_provider, provider_sub);"
    )


def downgrade() -> None:
    # Emergency rollback only (not a production flow). Microsoft-only rows have
    # no equivalent in the old schema: they are restored with
    # google_sub = provider_sub, which is harmless for a rollback but means a
    # re-upgrade would treat them as Google identities. The composite unique is
    # dropped before re-adding the single-column google_sub unique.
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_auth_provider_provider_sub_key;"
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(255);")
    op.execute("UPDATE users SET google_sub = provider_sub WHERE google_sub IS NULL;")
    op.execute("ALTER TABLE users ALTER COLUMN google_sub SET NOT NULL;")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_google_sub_key UNIQUE (google_sub);"
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS provider_sub;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS auth_provider;")
