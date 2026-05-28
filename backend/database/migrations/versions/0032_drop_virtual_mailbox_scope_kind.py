"""
Simplify ``virtual_mailboxes`` to a flat ``account_ids`` list.

The original shape (migration 0028) modelled a virtual mailbox with
``scope_kind ∈ {mailbox, all, accounts}`` plus a polymorphic
``scope_payload``. In practice the three branches always resolved to a
list of ``account_id`` values internally — the "category of scope"
gave the user an indirection (a definition that grew automatically as
they connected new accounts) but added complexity for both the API
boundary and the form. This migration collapses the model:

- The ``scope_kind`` column and its CHECK go away.
- ``scope_payload`` is normalised in place to ``{"account_ids":[...]}``.
- For existing rows with ``scope_kind = 'all'`` we **snapshot** every
  account the user owned at migration time into the list. For
  ``'mailbox'`` we snapshot the accounts of the referenced mailbox. The
  semantic change is intentional and documented in
  ``docs/features/bandejas-ficticias.md``: from now on a virtual
  mailbox is an explicit, static list of accounts. Newly connected
  accounts do NOT join legacy "scope=all" virtual mailboxes
  automatically.

If the snapshot lookup yields no rows (the referenced mailbox was
deleted, or the user owned no accounts), the list collapses to ``[]``.
The virtual mailbox remains valid and the read path returns an empty
listing — consistent with the existing "re-validate on every read"
contract.

The column name ``scope_payload`` is preserved (instead of renaming to
``account_ids``) to avoid rippling the rename through psycopg2-bound
queries / repositories that already address the column by name. The
projection in the queries module surfaces the list as ``account_ids``
to the service layer regardless.
"""
from __future__ import annotations

from alembic import op


revision = "0032_drop_virtual_mailbox_scope_kind"
down_revision = "0031_add_to_email_to_email_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Snapshot scope_kind='all' rows: every account the owner has.
    op.execute(
        """
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
        """
    )

    # 2. Snapshot scope_kind='mailbox' rows: accounts of the referenced
    # mailbox, but only if the mailbox still belongs to the owner.
    op.execute(
        """
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
        """
    )

    # 3. Normalise all rows so scope_payload has exactly the shape
    # ``{"account_ids":[...]}`` — drops residual keys (``mailbox_id``)
    # and guarantees every row carries the key even if empty. This step
    # also covers scope_kind='accounts' rows that already had the key.
    op.execute(
        """
        UPDATE virtual_mailboxes
        SET scope_payload = jsonb_build_object(
            'account_ids',
            COALESCE(scope_payload->'account_ids', '[]'::jsonb)
        );
        """
    )

    # 4. Drop the CHECK constraint. The constraint name follows the PG
    # convention for table-level checks created via ``CHECK (...)``
    # inline on a column definition: ``<table>_<column>_check``.
    op.execute(
        "ALTER TABLE virtual_mailboxes "
        "DROP CONSTRAINT IF EXISTS virtual_mailboxes_scope_kind_check;"
    )

    # 5. Drop the column.
    op.execute("ALTER TABLE virtual_mailboxes DROP COLUMN IF EXISTS scope_kind;")


def downgrade() -> None:
    # Re-add the column with a default. All existing rows collapse to
    # ``scope_kind='accounts'`` since that is the only branch whose
    # shape survived the upgrade. The snapshot is NOT reconstructed —
    # the downgrade is best-effort and intended only for a rollback in
    # development, where any post-migration data loss is acceptable.
    op.execute(
        """
        ALTER TABLE virtual_mailboxes
        ADD COLUMN IF NOT EXISTS scope_kind VARCHAR(20)
            NOT NULL DEFAULT 'accounts'
            CHECK (scope_kind IN ('mailbox', 'all', 'accounts'));
        """
    )
    op.execute(
        "ALTER TABLE virtual_mailboxes ALTER COLUMN scope_kind DROP DEFAULT;"
    )
