"""
Integration tests for the ``email_metadata.has_attachments`` invariant
(D-09).

Two checks against the real database:

1. **Invariant query** — the SQL probe documented in the plan must
   never return a row. It detects rows where the flag and the live
   count of non-inline attachment rows disagree (with one accepted
   asymmetry: ``has_attachments=false`` AND no rows is correct because
   the email may not have been opened yet — B.lazy puro).
2. **recompute_has_attachments side effect** — the helper called from
   ``emails_service.get_email_full_content`` must flip the flag from
   ``false`` -> ``true`` once non-inline attachments are inserted, and
   keep it consistent if the inline-only attachments are added without
   any user-visible downloadable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import psycopg2.extras
import pytest

from api.services.services_helpers import recompute_has_attachments


def _seed_account(isolated_db, user_id: str) -> tuple[str, str]:
    """Insert a mailbox + account owned by ``user_id`` and return their ids."""
    mailbox_id = str(uuid4())
    account_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            "INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id) "
            "VALUES (%s, %s, %s)",
            (mailbox_id, "Test mb", user_id),
        )
        cur.execute(
            """
            INSERT INTO accounts (
                account_id, mailbox_id, provider, display_label,
                created_at
            ) VALUES (
                %s, %s, 'gmail', 'test', NOW()
            )
            """,
            (account_id, mailbox_id),
        )
    return mailbox_id, account_id


def _insert_email(isolated_db, *, account_id: str, provider_message_id: str,
                  has_attachments: bool = False) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata (
                provider_message_id, account_id, thread_id,
                from_email, from_name, subject,
                received_at, is_read, box, has_attachments
            ) VALUES (%s, %s, 't1', 'a@b.com', 'A', 's',
                      %s, FALSE, 'ALL_MAIL', %s)
            """,
            (
                provider_message_id, account_id,
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                has_attachments,
            ),
        )


def _insert_attachment(
    isolated_db, *, account_id: str, provider_message_id: str,
    is_inline: bool, position: int = 0, part_id: str | None = "0.1",
) -> str:
    attachment_id = str(uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_attachments (
                attachment_id, account_id, provider_message_id, part_id,
                provider_attachment_id, filename, mime_type, size,
                content_id, is_inline, position
            ) VALUES (%s, %s, %s, %s, NULL, 'a.pdf', 'application/pdf', 10,
                      NULL, %s, %s)
            """,
            (
                attachment_id, account_id, provider_message_id, part_id,
                is_inline, position,
            ),
        )
    return attachment_id


_INVARIANT_QUERY = """
SELECT em.account_id, em.provider_message_id, em.has_attachments,
       COUNT(ea.attachment_id) AS attachment_count
FROM email_metadata em
LEFT JOIN email_attachments ea
       ON em.account_id = ea.account_id
      AND em.provider_message_id = ea.provider_message_id
      AND ea.is_inline = false
GROUP BY em.account_id, em.provider_message_id, em.has_attachments
HAVING (em.has_attachments = TRUE  AND COUNT(ea.attachment_id) = 0)
    OR (em.has_attachments = FALSE AND COUNT(ea.attachment_id) > 0);
"""


def _run_invariant_query(isolated_db) -> list[dict]:
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_INVARIANT_QUERY)
        return list(cur.fetchall())


class TestHasAttachmentsInvariant:

    def test_seeded_state_satisfies_invariant(self, isolated_db):
        # Migration 0010 ships an authenticated set of seeded emails; the
        # invariant must hold immediately after schema setup, before any
        # test mutations.
        rows = _run_invariant_query(isolated_db)
        assert rows == [], (
            f"Invariant violated by seeded data: {rows}"
        )

    def test_invariant_detects_flag_true_with_zero_rows(
        self, isolated_db, _seed_test_user,
    ):
        # Manually inject the broken state to confirm the detector works.
        # ``has_attachments=true`` but there are zero non-inline rows.
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id="bad-1",
            has_attachments=True,
        )
        rows = _run_invariant_query(isolated_db)
        violations = [
            r for r in rows
            if str(r["account_id"]) == account_id
            and r["provider_message_id"] == "bad-1"
        ]
        assert len(violations) == 1
        assert violations[0]["has_attachments"] is True
        assert violations[0]["attachment_count"] == 0

    def test_invariant_detects_flag_false_with_non_inline_rows(
        self, isolated_db, _seed_test_user,
    ):
        # Inverse: the flag is false but there is a non-inline attachment
        # row. This is the signal that ``recompute_has_attachments`` was
        # not called after the upsert — a bug in the service flow.
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id="bad-2",
            has_attachments=False,
        )
        _insert_attachment(
            isolated_db,
            account_id=account_id,
            provider_message_id="bad-2",
            is_inline=False,
        )
        rows = _run_invariant_query(isolated_db)
        violations = [
            r for r in rows
            if str(r["account_id"]) == account_id
            and r["provider_message_id"] == "bad-2"
        ]
        assert len(violations) == 1
        assert violations[0]["has_attachments"] is False
        assert violations[0]["attachment_count"] == 1

    def test_invariant_passes_when_flag_false_and_only_inline_rows(
        self, isolated_db, _seed_test_user,
    ):
        # The B.lazy semantics keep ``has_attachments=false`` when only
        # inline images exist — those are not user-visible downloadables.
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id="ok-inline",
            has_attachments=False,
        )
        _insert_attachment(
            isolated_db,
            account_id=account_id,
            provider_message_id="ok-inline",
            is_inline=True,
        )
        rows = _run_invariant_query(isolated_db)
        violations = [
            r for r in rows
            if str(r["account_id"]) == account_id
            and r["provider_message_id"] == "ok-inline"
        ]
        assert violations == []


class TestRecomputeHasAttachments:

    def test_flag_flipped_to_true_after_non_inline_insert_and_recompute(
        self, isolated_db, _seed_test_user,
    ):
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        provider_message_id = "lazy-1"
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            has_attachments=False,
        )
        _insert_attachment(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            is_inline=False,
        )
        recompute_has_attachments(account_id, provider_message_id)
        with isolated_db.cursor() as cur:
            cur.execute(
                "SELECT has_attachments FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            assert cur.fetchone()[0] is True

    def test_flag_stays_false_when_only_inline_attachments(
        self, isolated_db, _seed_test_user,
    ):
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        provider_message_id = "inline-only"
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            has_attachments=False,
        )
        _insert_attachment(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            is_inline=True,
        )
        recompute_has_attachments(account_id, provider_message_id)
        with isolated_db.cursor() as cur:
            cur.execute(
                "SELECT has_attachments FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            assert cur.fetchone()[0] is False

    def test_flag_idempotent(self, isolated_db, _seed_test_user):
        from tests.integration.conftest import TEST_USER_ID
        _mailbox_id, account_id = _seed_account(isolated_db, TEST_USER_ID)
        provider_message_id = "idem-1"
        _insert_email(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            has_attachments=False,
        )
        _insert_attachment(
            isolated_db,
            account_id=account_id,
            provider_message_id=provider_message_id,
            is_inline=False,
        )
        # Calling twice must yield the same result.
        recompute_has_attachments(account_id, provider_message_id)
        recompute_has_attachments(account_id, provider_message_id)
        with isolated_db.cursor() as cur:
            cur.execute(
                "SELECT has_attachments FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            assert cur.fetchone()[0] is True
