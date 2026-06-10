"""Convert every ``drafts.body`` from plain text to HTML.

The rich-text composer makes the draft ``body`` an HTML document end to
end. There is **no** ``body_format`` discriminator — from this migration
on, ``drafts.body`` is always HTML. Drafts that predate the editor were
stored as plain text, so this one-shot data migration rewrites each
existing row to its HTML equivalent (mirrors
``core.email.helpers.plain_text_to_html``):

- HTML-escape ``&`` / ``<`` / ``>`` FIRST (the text lands in element
  content, never inside an attribute, so escaping quotes is unnecessary).
  ``&`` must be escaped before ``<`` / ``>`` so their ``&lt;`` / ``&gt;``
  are not double-escaped.
- THEN convert line breaks: ``\\r\\n`` / ``\\r`` / ``\\n`` become ``<br>``
  and the whole body is wrapped in a single ``<p>…</p>``.

The conversion is the same SQL the fallback runner applies (kept in
lockstep — see ``migrations/runner.py``). It is **idempotent**: only rows
with a non-blank body that does NOT already look like HTML (no leading
``<p`` / no ``<br``) are touched, so a re-run never double-escapes a row
that is already HTML. Empty / whitespace-only bodies stay ``''``.

Only ``drafts`` is converted: sent emails (``email_metadata`` /
``email_content``) are historical and rendered by the inbound pipeline;
they are never reopened in the editor. The companion provider-parse change
(``_parse_gmail_draft`` / ``_parse_outlook_draft``) converts any legacy
plain-text draft that re-enters via ``sync_drafts``, so migration + parse
cover the old-draft case together.
"""
from __future__ import annotations

from alembic import op


revision = "0034_convert_drafts_body_to_html"
down_revision = "0033_index_email_metadata_thread"
branch_labels = None
depends_on = None


# Escape ``&`` / ``<`` / ``>`` (order matters), normalise CR/LF to ``\n``,
# turn each ``\n`` into ``<br>`` and wrap in a single ``<p>…</p>``. Guarded
# so blank or already-HTML rows are left untouched (idempotent).
_CONVERT_DRAFTS_BODY_SQL = r"""
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
"""


def upgrade() -> None:
    op.execute(_CONVERT_DRAFTS_BODY_SQL)


def downgrade() -> None:
    # Best-effort: drafts are ephemeral, so a byte-exact reversal is not
    # required. Strip the wrapping markup and unescape entities to recover
    # a plain-text approximation (``<br>`` / ``</p>`` become newlines).
    op.execute(
        r"""
        UPDATE drafts
        SET body = btrim(
            replace(
                replace(
                    replace(
                        regexp_replace(
                            regexp_replace(body, '<\s*br\s*/?>', E'\n', 'gi'),
                            '<[^>]+>', '', 'g'),
                        '&lt;', '<'),
                    '&gt;', '>'),
                '&amp;', '&')
        )
        WHERE body LIKE '<p>%';
        """
    )
