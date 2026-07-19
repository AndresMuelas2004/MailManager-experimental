"""
Invalidate the ``email_content`` cache after the ``<style>`` rendering fixes.

The inbound sanitiser gained three rendering fixes that change the shape of
the sanitised body: (1) bleach's HTML-escaping inside ``<style>`` rawtext is
now undone (``&gt;`` combinators no longer split selectors at CSS-parse time,
which turned scoped Outlook dark-mode hack rules like ``[data-ogsc] .card >
table {…}`` into global ``table {…}`` rules painting dark backgrounds in the
light viewer); (2) ``@media`` blocks conditioned on ``prefers-color-scheme``
are dropped (Gmail parity — the viewer is light-only); (3) declarations using
``var(…)`` are dropped everywhere (their ``--x`` definitions never survive,
and the broken uses overrode the legacy ``bgcolor`` fallbacks). Bodies cached
before these fixes still carry the escaped combinators / dark rules / broken
``var()`` uses, so they are cleared once and repopulate on demand via
cache-aside (the same one-shot invalidation pattern as migrations 0014-0019,
0024, 0035, 0040, 0043, 0044). In a fresh bootstrap the table is already
empty, so it is a no-op.
"""
from __future__ import annotations

from alembic import op


revision = "0046_invalidate_email_content_cache_style_fixes"
down_revision = "0045_create_folders_and_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE email_content;")


def downgrade() -> None:
    # One-shot cache invalidation; the cleared rows repopulate on demand via
    # cache-aside, so there is nothing to revert.
    pass
