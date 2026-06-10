"""
Integration test for migration ``0034_convert_drafts_body_to_html``.

The migration is a one-shot data conversion: every existing plain-text
``drafts.body`` is rewritten to HTML (escape ``&`` / ``<`` / ``>`` first,
then ``nl2br`` wrapped in a single ``<p>``). The same SQL lives in BOTH the
Alembic file and the fallback ``migrations/runner.py`` (kept in lockstep), so
this test imports the canonical statement from the Alembic module and applies
it to seeded ``drafts`` rows inside the per-test rollback transaction.

It locks the behaviours the migration must guarantee:
- plain text with ``&`` / ``<`` / ``>`` and line breaks → escaped + ``<br>``;
- carriage returns (CRLF and a lone CR) normalise to ``<br>`` just like a
  bare line feed (two dedicated replace() steps that no other test exercises);
- a row that already looks like HTML (``<p…`` or contains ``<br``) → untouched
  (idempotent — a re-run must not double-escape);
- empty / whitespace-only body → stays ``''``;
- the SQL mirrored in ``migrations/runner.py`` stays equivalent (whitespace-
  normalised) to the Alembic copy — the documented lockstep that the fallback
  startup path relies on.
"""

from __future__ import annotations

import importlib

import psycopg2.extras

from tests.integration.test_drafts import _insert_draft

# The migration module's filename starts with a digit, so it cannot be a
# normal ``import``; load it by its dotted path with ``importlib``.
migration_0033 = importlib.import_module(
    "database.migrations.versions.0034_convert_drafts_body_to_html"
)


def _apply_conversion(isolated_db) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(migration_0033._CONVERT_DRAFTS_BODY_SQL)


def _body_of(isolated_db, provider_draft_id: str, account_id: str) -> str:
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT body FROM drafts WHERE provider_draft_id = %s AND account_id = %s::uuid",
            (provider_draft_id, account_id),
        )
        return cur.fetchone()["body"]


def test_converts_plain_text_escaping_and_linebreaks(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-plain",
        body="a & b < c > d\nsecond line",
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-plain", aid) == (
        "<p>a &amp; b &lt; c &gt; d<br>second line</p>"
    )


def test_already_html_row_left_untouched(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # A row that already starts with ``<p`` must not be re-wrapped /
    # double-escaped — the guard makes the conversion idempotent.
    mid, aid = setup_mailbox_and_account(test_client)
    html = "<p>already &amp; html</p>"
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-html",
        body=html,
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-html", aid) == html


def test_row_containing_br_left_untouched(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # The guard also skips rows that already contain ``<br`` in the first 64
    # chars (another idempotency signal).
    mid, aid = setup_mailbox_and_account(test_client)
    html = "text<br>more"
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-br",
        body=html,
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-br", aid) == html


def test_empty_body_stays_empty(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-empty", body="",
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-empty", aid) == ""


def test_whitespace_only_body_stays_unchanged(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # ``btrim(body) <> ''`` excludes whitespace-only bodies, so they are not
    # wrapped in a <p>.
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-ws", body="   ",
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-ws", aid) == "   "


def test_conversion_is_idempotent_on_rerun(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Running the conversion twice must produce the same result as once
    # (the post-conversion row starts with ``<p`` and is skipped).
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-idem",
        body="x & y",
    )
    _apply_conversion(isolated_db)
    once = _body_of(isolated_db, "d-idem", aid)
    _apply_conversion(isolated_db)
    twice = _body_of(isolated_db, "d-idem", aid)
    assert once == twice == "<p>x &amp; y</p>"


def test_carriage_returns_normalised_to_br(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # The conversion normalises CR/LF to LF BEFORE the ``\n`` -> ``<br>`` step,
    # via two dedicated replace() calls (E'\r\n' -> E'\n', then E'\r' -> E'\n').
    # Every other test feeds only ``\n``, so those two branches are otherwise
    # unexercised: a regression that dropped or reordered them would pass
    # silently. Feed both a CRLF and a bare CR.
    mid, aid = setup_mailbox_and_account(test_client)
    _insert_draft(
        isolated_db, account_id=aid, provider_draft_id="d-crlf",
        body="a\r\nb\rc",
    )
    _apply_conversion(isolated_db)
    assert _body_of(isolated_db, "d-crlf", aid) == "<p>a<br>b<br>c</p>"


def test_runner_0034_sql_stays_in_lockstep_with_alembic():
    # The 0034 conversion SQL is duplicated: the canonical copy lives in the
    # Alembic module (exercised by the tests above) and an equivalent copy
    # lives in the fallback ``migrations/runner.py`` (what runs at container
    # startup when Alembic is skipped). database_guide.md requires the two
    # kept in lockstep. They differ only in indentation, so compare with
    # whitespace collapsed. This is a pure static check (no DB) that defends
    # the mirror against a silent one-sided edit.
    from database.migrations import runner

    def _norm(sql: str) -> str:
        return " ".join(sql.split())

    target = _norm(migration_0033._CONVERT_DRAFTS_BODY_SQL)
    matches = [stmt for stmt in runner._DDL_STATEMENTS if _norm(stmt) == target]
    assert len(matches) == 1, (
        "runner.py must contain exactly one statement equal (whitespace-"
        f"normalised) to the Alembic 0034 conversion SQL; found {len(matches)}. "
        "A drift between the two copies ships a broken fallback startup migration."
    )
