"""
E2E full-flow tests — real Gmail and Outlook APIs, no fakes.

Each test checks its own prerequisites via flow_state keys.
If a prerequisite is missing (because the producing test failed),
the dependent test is SKIPPED. Independent tests always run.

Run with: python -m pytest backend/tests/e2e -v --tb=short
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import pytest

from ._forward_helpers import bootstrap_attachment_message
from .e2e_config import (
    GMAIL_ACCOUNT_ID,
    GMAIL_MAILBOX_ID,
    OUTLOOK_ACCOUNT_ID,
    OUTLOOK_MAILBOX_ID,
    SEND_RECIPIENT,
    TEST_USER_ID,
)


def _assert_ok(response, *, expected: int = 200) -> None:
    assert response.status_code == expected, response.text


def _subject_sort_ranks_via_db(subjects: list[str]) -> list[int]:
    """Rank each subject by the EXACT order PostgreSQL gives the backend.

    The backend orders ``subject`` by ``lower(unaccent(coalesce(subject, '')))``
    (``_SORT_EXPRESSIONS['subject']``). Two things make a pure-Python mirror
    wrong, so we ask the DB itself for the order instead of reimplementing it:

    1. **Accent folding** — ``unaccent`` strips diacritics before lowercasing.
    2. **Collation** — once transformed, PostgreSQL compares the text under the
       DATABASE collation (``en_US.utf8`` here), NOT by Unicode code point.
       These disagree on space/punctuation-vs-digit ordering: under
       ``en_US.utf8`` the space is low-weight, so ``'10 €'`` sorts BEFORE
       ``'1 oferta'``, whereas a code-point ``sorted()`` puts ``'1 oferta'``
       first (space 0x20 < '0' 0x30). The old helper folded accents but then
       relied on Python's code-point ``sorted()``, which is NOT the backend's
       order — this test was asserting against the wrong comparator.

    Returns, for each input subject (positionally), a non-negative integer
    rank such that ``rank[i] <= rank[j]`` iff PostgreSQL orders subject *i* at
    or before subject *j* under the backend's sort key. Ties (equal sort keys)
    share the same rank, so a page in correct order yields a NON-DECREASING
    rank sequence regardless of how the backend breaks ties
    (``account_id, provider_message_id``)."""
    if not subjects:
        return []
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            # DENSE_RANK over the distinct sort keys gives every subject sharing
            # a key the SAME rank; ``WITH ORDINALITY`` preserves the input order
            # so the result maps back positionally to ``subjects``.
            cur.execute(
                """
                SELECT dense_rank() OVER (
                           ORDER BY lower(unaccent(coalesce(s, '')))
                       )
                FROM unnest(%s::text[]) WITH ORDINALITY AS u(s, ord)
                ORDER BY u.ord
                """,
                (subjects,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _require(flow_state: dict, *keys: str) -> None:
    """Skip if any required flow_state keys are missing."""
    missing = [k for k in keys if k not in flow_state]
    if missing:
        pytest.skip(f"Prerequisites not met: {', '.join(missing)}")


def _db_conn():
    """Create a psycopg2 connection from DATABASE_URL."""
    dsn = os.getenv("DATABASE_URL", "").strip()
    return psycopg2.connect(dsn=dsn)


def _fetch_email_ids(
    account_id: str, limit: int, box_filter: str = "ALL_MAIL",
) -> list[str]:
    """Fetch provider_message_ids from DB for the given account and box."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box = %s LIMIT %s",
                (account_id, box_filter, limit),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _count_by_box(account_id: str, box: str) -> int:
    """Count emails in a given box for an account."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM email_metadata "
                "WHERE account_id = %s AND box = %s",
                (account_id, box),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _count_unread_by_box(account_id: str, box: str) -> int:
    """Count UNREAD (is_read=FALSE) emails in a given box for an account.

    The sibling ``_count_by_box`` counts every row regardless of read state;
    the unread-count endpoint counts only is_read=FALSE, so it needs its own
    control query.
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM email_metadata "
                "WHERE account_id = %s AND box = %s AND is_read = FALSE",
                (account_id, box),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _boxes_for_ids(account_id: str, ids: list[str]) -> dict[str, str]:
    """Return {provider_message_id: box} for the given IDs under an account."""
    if not ids:
        return {}
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id, box FROM email_metadata "
                "WHERE account_id = %s AND provider_message_id = ANY(%s)",
                (account_id, ids),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _ids_in_box(account_id: str, box: str) -> set[str]:
    """Return the full set of provider_message_ids currently in `box` for an account."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box = %s",
                (account_id, box),
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _clear_sync_cursor(account_id: str) -> None:
    """Set sync_cursor to NULL so the next sync exercises Path 1 (bootstrap)."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET sync_cursor = NULL WHERE account_id = %s",
                (account_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _fetch_one_message_id(account_id: str) -> str | None:
    """Fetch a single provider_message_id for the account, or None."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s LIMIT 1",
                (account_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _delete_email_content(account_id: str, provider_message_id: str) -> None:
    """Remove any cached content row so the next GET exercises the MISS path."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM email_content "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
        conn.commit()
    finally:
        conn.close()


def _fetch_email_content_row(account_id: str, provider_message_id: str):
    """Return (html_body, text_body, fetched_at) or None."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT html_body, text_body, fetched_at FROM email_content "
                "WHERE account_id = %s AND provider_message_id = %s",
                (account_id, provider_message_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ===================================================================
# Section 1: Health
# ===================================================================

def test_01_health_check(e2e_client):
    response = e2e_client.get("/health")
    _assert_ok(response)
    assert response.json() == {"status": "ok"}


# ===================================================================
# Section 2: Auth read
# ===================================================================

def test_02_get_auth_me(e2e_client):
    response = e2e_client.get("/auth/me")
    _assert_ok(response)
    assert response.json()["user_id"] == TEST_USER_ID


# ===================================================================
# Section 3: CRUD (temp mailbox + accounts)
# ===================================================================

def test_03_create_mailbox(e2e_client, flow_state, created_resources):
    response = e2e_client.post("/mailboxes", json={"display_name": "E2E Temp Mailbox"})
    _assert_ok(response)
    data = response.json()
    assert data["display_name"] == "E2E Temp Mailbox"
    mid = data["mailbox_id"]
    flow_state["temp_mid"] = mid
    created_resources["mailbox_ids"].append(mid)


def test_04_create_gmail_account(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.post(
        f"/mailboxes/{flow_state['temp_mid']}/accounts",
        json={"provider": "gmail", "display_label": "e2e-gmail-temp"},
    )
    _assert_ok(response)
    flow_state["temp_gmail_id"] = response.json()["account_id"]


def test_05_create_outlook_account(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.post(
        f"/mailboxes/{flow_state['temp_mid']}/accounts",
        json={"provider": "outlook", "display_label": "e2e-outlook-temp"},
    )
    _assert_ok(response)
    flow_state["temp_outlook_id"] = response.json()["account_id"]


def test_06_list_mailboxes(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.get("/mailboxes")
    _assert_ok(response)
    ids = [m["mailbox_id"] for m in response.json()]
    assert flow_state["temp_mid"] in ids


def test_07_get_mailbox_detail(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.get(f"/mailboxes/{flow_state['temp_mid']}")
    _assert_ok(response)
    assert response.json()["mailbox_id"] == flow_state["temp_mid"]


def test_07b_rename_mailbox(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.patch(
        f"/mailboxes/{flow_state['temp_mid']}",
        json={"display_name": "E2E Renamed Mailbox"},
    )
    _assert_ok(response)
    assert response.json()["display_name"] == "E2E Renamed Mailbox"
    # The rename persisted: a fresh GET reflects the new name (same test, per
    # common_mistakes.md §1 — a follow-up read is not a separate behaviour).
    detail = e2e_client.get(f"/mailboxes/{flow_state['temp_mid']}")
    _assert_ok(detail)
    assert detail.json()["display_name"] == "E2E Renamed Mailbox"


def test_08_list_accounts(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.get(f"/mailboxes/{flow_state['temp_mid']}/accounts")
    _assert_ok(response)
    assert len(response.json()) >= 2


def test_09_get_gmail_account_detail(e2e_client, flow_state):
    _require(flow_state, "temp_gmail_id")
    response = e2e_client.get(
        f"/mailboxes/{flow_state['temp_mid']}/accounts/{flow_state['temp_gmail_id']}"
    )
    _assert_ok(response)
    assert response.json()["account_id"] == flow_state["temp_gmail_id"]


def test_10_get_outlook_account_detail(e2e_client, flow_state):
    _require(flow_state, "temp_outlook_id")
    response = e2e_client.get(
        f"/mailboxes/{flow_state['temp_mid']}/accounts/{flow_state['temp_outlook_id']}"
    )
    _assert_ok(response)
    assert response.json()["account_id"] == flow_state["temp_outlook_id"]


def test_11_patch_account_label(e2e_client, flow_state):
    _require(flow_state, "temp_gmail_id")
    response = e2e_client.patch(
        f"/mailboxes/{flow_state['temp_mid']}/accounts/{flow_state['temp_gmail_id']}",
        json={"display_label": "e2e-gmail-renamed"},
    )
    _assert_ok(response)
    assert response.json()["display_label"] == "e2e-gmail-renamed"


def test_12_delete_account(e2e_client, flow_state):
    _require(flow_state, "temp_outlook_id")
    response = e2e_client.delete(
        f"/mailboxes/{flow_state['temp_mid']}/accounts/{flow_state['temp_outlook_id']}"
    )
    _assert_ok(response)
    assert response.json() == {"status": "deleted"}


def test_13_delete_mailbox(e2e_client, flow_state):
    _require(flow_state, "temp_mid")
    response = e2e_client.delete(f"/mailboxes/{flow_state['temp_mid']}")
    _assert_ok(response)
    assert response.json() == {"status": "deleted"}
    # The mailbox is gone: a follow-up GET returns 404. Kept in the same test
    # per common_mistakes.md §1 — verifying the delete's side effect is not a
    # separate behaviour.
    gone = e2e_client.get(f"/mailboxes/{flow_state['temp_mid']}")
    _assert_ok(gone, expected=404)


# ===================================================================
# Section 4: Provider operations (pre-existing connected accounts)
# ===================================================================

def test_15_sync_metadata_gmail_path_1(e2e_client, flow_state):
    _clear_sync_cursor(GMAIL_ACCOUNT_ID)
    response = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata",
        params={"account_id": GMAIL_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    assert isinstance(data["total_synced"], int)
    assert data["total_synced"] >= 0
    accounts = data["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == GMAIL_ACCOUNT_ID
    assert accounts[0]["sync_cursor"] is not None
    # A single healthy account also reports no partial failures.
    assert data["failed_accounts"] == []
    flow_state["gmail_path1_done"] = "true"


def test_16_sync_metadata_outlook_path_1(e2e_client, flow_state):
    _clear_sync_cursor(OUTLOOK_ACCOUNT_ID)
    response = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata",
        params={"account_id": OUTLOOK_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    assert isinstance(data["total_synced"], int)
    assert data["total_synced"] >= 0
    accounts = data["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == OUTLOOK_ACCOUNT_ID
    assert accounts[0]["sync_cursor"] is not None
    flow_state["outlook_path1_done"] = "true"


def test_17_sync_metadata_gmail_path_2(e2e_client, flow_state):
    response = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(response)
    data = response.json()
    assert isinstance(data["total_synced"], int)
    assert data["total_synced"] >= 0
    accounts = data["accounts"]
    synced_ids = {a["account_id"] for a in accounts}
    assert GMAIL_ACCOUNT_ID in synced_ids
    gmail_account = next(a for a in accounts if a["account_id"] == GMAIL_ACCOUNT_ID)
    assert gmail_account["sync_cursor"] is not None
    # Unified sync contract (Option A): with every test account healthy the
    # partial-failure list is empty. A real disconnected-account path is NOT
    # exercised here — there is no non-interactive way to expire a test
    # account's token and the E2E rules forbid touching their tokens; that
    # branch is covered by the unit / integration suites.
    assert data["failed_accounts"] == []


def test_18_sync_metadata_outlook_path_2(e2e_client, flow_state):
    response = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(response)
    data = response.json()
    assert isinstance(data["total_synced"], int)
    assert data["total_synced"] >= 0
    accounts = data["accounts"]
    synced_ids = {a["account_id"] for a in accounts}
    assert OUTLOOK_ACCOUNT_ID in synced_ids
    outlook_account = next(a for a in accounts if a["account_id"] == OUTLOOK_ACCOUNT_ID)
    assert outlook_account["sync_cursor"] is not None


def test_19_send_email_gmail(e2e_client):
    # The body is rich-text HTML: this exercises the real HTML send path
    # (Gmail assembles a multipart/alternative — derived text/plain + the
    # text/html leg — and the provider must accept it without rejection).
    response = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/send",
        json={
            "account_id": GMAIL_ACCOUNT_ID,
            "subject": "E2E automated test — Gmail send",
            "body": "<p>Automated E2E test email sent via <strong>Gmail</strong>.</p>",
            "recipients": [SEND_RECIPIENT],
        },
    )
    _assert_ok(response)
    assert response.json()["status"] == "sent"


def test_20_send_email_outlook(e2e_client):
    # The body is rich-text HTML: Graph stores it as contentType=HTML. The
    # send path must accept the HTML body without rejection.
    response = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/send",
        json={
            "account_id": OUTLOOK_ACCOUNT_ID,
            "subject": "E2E automated test — Outlook send",
            "body": "<p>Automated E2E test email sent via <strong>Outlook</strong>.</p>",
            "recipients": [SEND_RECIPIENT],
        },
    )
    _assert_ok(response)
    assert response.json()["status"] == "sent"


def test_21_update_read_status_gmail(e2e_client, flow_state):
    """Sync metadata, pick a message, mark as read."""
    # Sync first to ensure email_metadata rows exist in DB for the test account
    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_id = _fetch_one_message_id(GMAIL_ACCOUNT_ID)
    if msg_id is None:
        pytest.skip("No synced emails found for Gmail test account")
    response = e2e_client.patch(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": GMAIL_ACCOUNT_ID, "provider_message_id": msg_id}],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["updated_count"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == GMAIL_ACCOUNT_ID
    flow_state["gmail_read_status_done"] = "true"


def test_22_update_read_status_outlook(e2e_client, flow_state):
    """Sync metadata, pick a message, mark as unread."""
    # Sync first to ensure email_metadata rows exist in DB for the test account
    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_id = _fetch_one_message_id(OUTLOOK_ACCOUNT_ID)
    if msg_id is None:
        pytest.skip("No synced emails found for Outlook test account")
    response = e2e_client.patch(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/read-status",
        json={
            "is_read": False,
            "items": [{"account_id": OUTLOOK_ACCOUNT_ID, "provider_message_id": msg_id}],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["updated_count"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == OUTLOOK_ACCOUNT_ID


# ===================================================================
# Section 4b: Spam operations (pre-existing connected accounts)
# ===================================================================

def test_23_move_to_spam_gmail(e2e_client, flow_state):
    """Sync, pick 10 emails, move to spam, verify DB."""
    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_ids = _fetch_email_ids(GMAIL_ACCOUNT_ID, 10, "ALL_MAIL")
    if len(msg_ids) < 10:
        pytest.skip("Not enough ALL_MAIL emails for Gmail spam test")

    response = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/spam",
        json={
            "items": [
                {"account_id": GMAIL_ACCOUNT_ID, "provider_message_id": mid}
                for mid in msg_ids
            ],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["moved_count"] == 10
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == GMAIL_ACCOUNT_ID
    assert data["accounts"][0]["moved"] == 10

    # Verify each moved email is now in SPAM (robust to concurrent provider events).
    boxes = _boxes_for_ids(GMAIL_ACCOUNT_ID, msg_ids)
    for mid in msg_ids:
        assert boxes.get(mid) == "SPAM", (
            f"Expected Gmail email {mid} to be in SPAM, got {boxes.get(mid)}"
        )

    flow_state["gmail_spam_done"] = "true"


def test_24_restore_from_spam_gmail(e2e_client, flow_state):
    """Pick 10 spam emails, restore, verify DB."""
    _require(flow_state, "gmail_spam_done")

    msg_ids = _fetch_email_ids(GMAIL_ACCOUNT_ID, 10, "SPAM")
    if len(msg_ids) < 10:
        pytest.skip("Not enough SPAM emails for Gmail restore test")

    response = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/restore-from-spam",
        json={
            "items": [
                {"account_id": GMAIL_ACCOUNT_ID, "provider_message_id": mid}
                for mid in msg_ids
            ],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["moved_count"] == 10
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == GMAIL_ACCOUNT_ID
    assert data["accounts"][0]["moved"] == 10

    # Verify each restored email is no longer in SPAM (robust to concurrent provider events).
    boxes = _boxes_for_ids(GMAIL_ACCOUNT_ID, msg_ids)
    for mid in msg_ids:
        assert boxes.get(mid) != "SPAM", (
            f"Expected Gmail email {mid} to be out of SPAM, still in {boxes.get(mid)}"
        )

    flow_state["gmail_restore_done"] = "true"


def test_25_move_to_spam_outlook(e2e_client, flow_state):
    """Sync, pick 10 emails, move to spam, verify DB, stash new SPAM IDs."""
    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_ids = _fetch_email_ids(OUTLOOK_ACCOUNT_ID, 10, "ALL_MAIL")
    if len(msg_ids) < 10:
        pytest.skip("Not enough ALL_MAIL emails for Outlook spam test")

    # Outlook mutates provider_message_id when moving between folders. The IDs
    # we send are NOT the IDs that end up in SPAM after the move — Outlook
    # assigns new ones. To identify the 10 rows we just created, snapshot the
    # set of SPAM IDs before the move and take the delta after.
    spam_before_ids = _ids_in_box(OUTLOOK_ACCOUNT_ID, "SPAM")

    response = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/spam",
        json={
            "items": [
                {"account_id": OUTLOOK_ACCOUNT_ID, "provider_message_id": mid}
                for mid in msg_ids
            ],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["moved_count"] == 10
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == OUTLOOK_ACCOUNT_ID
    assert data["accounts"][0]["moved"] == 10

    # Compute the delta: IDs that appeared in SPAM as a result of the move.
    spam_after_ids = _ids_in_box(OUTLOOK_ACCOUNT_ID, "SPAM")
    newly_spam_ids = sorted(spam_after_ids - spam_before_ids)
    assert len(newly_spam_ids) == 10, (
        f"Expected 10 new SPAM IDs after move, got {len(newly_spam_ids)}"
    )

    # Verify DB consistency for each new ID.
    boxes = _boxes_for_ids(OUTLOOK_ACCOUNT_ID, newly_spam_ids)
    for mid in newly_spam_ids:
        assert boxes.get(mid) == "SPAM", (
            f"Expected Outlook email {mid} to be in SPAM, got {boxes.get(mid)}"
        )

    # Stash for test 26 — these are the exact IDs it should attempt to restore,
    # avoiding stale residue from previous E2E runs.
    flow_state["outlook_spam_moved_ids"] = newly_spam_ids
    flow_state["outlook_spam_done"] = "true"


def test_26_restore_from_spam_outlook(e2e_client, flow_state):
    """Restore the exact 10 SPAM IDs that test 25 just created."""
    _require(flow_state, "outlook_spam_done", "outlook_spam_moved_ids")

    msg_ids = flow_state["outlook_spam_moved_ids"]
    assert len(msg_ids) == 10, (
        f"Expected 10 IDs from flow_state, got {len(msg_ids)}"
    )

    response = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/restore-from-spam",
        json={
            "items": [
                {"account_id": OUTLOOK_ACCOUNT_ID, "provider_message_id": mid}
                for mid in msg_ids
            ],
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["moved_count"] == 10
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == OUTLOOK_ACCOUNT_ID
    assert data["accounts"][0]["moved"] == 10

    # After restore, none of the original 10 IDs should still be in SPAM.
    # Outlook mutates IDs again on this second move, so the DB row for the
    # old ID is either gone (replaced by a new one) or its box is no longer
    # 'SPAM'.
    boxes = _boxes_for_ids(OUTLOOK_ACCOUNT_ID, msg_ids)
    for mid in msg_ids:
        assert boxes.get(mid) != "SPAM", (
            f"Expected Outlook email {mid} to be out of SPAM after restore, "
            f"still in {boxes.get(mid)}"
        )


# ===================================================================
# Section 5: Trash lifecycle (move to trash -> restore -> delete)
# ===================================================================

def test_27_move_to_trash(e2e_client, flow_state):
    """Sync both providers, pick 4 non-TRASH emails, move them to trash."""
    _require(flow_state, "gmail_path1_done", "outlook_path1_done")

    dsn = os.getenv("DATABASE_URL", "").strip()
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box NOT IN ('TRASH', 'DELETED') "
                "LIMIT 2",
                (GMAIL_ACCOUNT_ID,),
            )
            gmail_ids = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box NOT IN ('TRASH', 'DELETED') "
                "LIMIT 2",
                (OUTLOOK_ACCOUNT_ID,),
            )
            outlook_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    assert len(gmail_ids) >= 2, "Need at least 2 Gmail emails for trash lifecycle"
    assert len(outlook_ids) >= 2, "Need at least 2 Outlook emails for trash lifecycle"

    flow_state["trash_gmail_ids"] = gmail_ids
    flow_state["trash_outlook_ids"] = outlook_ids

    items = (
        [{"provider_message_id": mid, "account_id": GMAIL_ACCOUNT_ID} for mid in gmail_ids]
        + [{"provider_message_id": mid, "account_id": OUTLOOK_ACCOUNT_ID} for mid in outlook_ids]
    )

    # Gmail move-to-trash
    gmail_items = [i for i in items if i["account_id"] == GMAIL_ACCOUNT_ID]
    resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/move-to-trash",
        json={"items": gmail_items},
    )
    _assert_ok(resp)

    # Outlook move-to-trash
    outlook_items = [i for i in items if i["account_id"] == OUTLOOK_ACCOUNT_ID]
    resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/move-to-trash",
        json={"items": outlook_items},
    )
    _assert_ok(resp)

    # Verify DB state
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            # Gmail IDs don't change — verify by provider_message_id
            for mid in gmail_ids:
                cur.execute(
                    "SELECT box, previous_box FROM email_metadata "
                    "WHERE provider_message_id = %s AND account_id = %s",
                    (mid, GMAIL_ACCOUNT_ID),
                )
                row = cur.fetchone()
                assert row is not None, f"Gmail email {mid} not found"
                assert row[0] == "TRASH", f"Gmail email {mid} box should be TRASH, got {row[0]}"
                assert row[1] is not None, f"Gmail email {mid} previous_box should be set"

            # Outlook IDs change on move, so we cannot rely on the pre-move IDs.
            # Fetch any 2 TRASH rows for this account so later tests (28-31)
            # can operate on real current IDs. Filter by previous_box IS NOT NULL
            # to prefer rows that were just moved by our app when possible, but
            # fall back to any TRASH row if no such rows are found (rows that
            # had the ID mutated may have lost the previous_box linkage in the
            # upsert path, which is an Outlook-specific quirk).
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box = 'TRASH' "
                "ORDER BY (previous_box IS NOT NULL) DESC, provider_message_id "
                "LIMIT %s",
                (OUTLOOK_ACCOUNT_ID, len(outlook_ids)),
            )
            new_outlook_ids = [row[0] for row in cur.fetchall()]
            assert len(new_outlook_ids) >= len(outlook_ids), (
                f"Expected at least {len(outlook_ids)} Outlook TRASH emails, got {len(new_outlook_ids)}"
            )
            flow_state["trash_outlook_ids"] = new_outlook_ids
    finally:
        conn.close()

    flow_state["move_to_trash_done"] = "true"


def test_28_restore_gmail_from_trash(e2e_client, flow_state):
    """Restore 1 Gmail email from trash — verify restored to original box."""
    _require(flow_state, "move_to_trash_done")
    gmail_id = flow_state["trash_gmail_ids"][0]

    resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": gmail_id, "account_id": GMAIL_ACCOUNT_ID}],
        },
    )
    _assert_ok(resp)
    assert resp.json()["affected"] == 1

    dsn = os.getenv("DATABASE_URL", "").strip()
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT box, previous_box FROM email_metadata "
                "WHERE provider_message_id = %s AND account_id = %s",
                (gmail_id, GMAIL_ACCOUNT_ID),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] != "TRASH", f"Gmail email should be restored, got box={row[0]}"
            assert row[1] is None, "previous_box should be NULL after restore"
    finally:
        conn.close()

    flow_state["gmail_trash_restore_done"] = "true"


def test_29_delete_gmail_from_trash(e2e_client, flow_state):
    """Delete 1 Gmail email from trash — verify marked as DELETED."""
    _require(flow_state, "move_to_trash_done")
    gmail_id = flow_state["trash_gmail_ids"][1]

    resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/trash",
        json={
            "action": "delete",
            "items": [{"provider_message_id": gmail_id, "account_id": GMAIL_ACCOUNT_ID}],
        },
    )
    _assert_ok(resp)
    assert resp.json()["affected"] == 1

    dsn = os.getenv("DATABASE_URL", "").strip()
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT box FROM email_metadata "
                "WHERE provider_message_id = %s AND account_id = %s",
                (gmail_id, GMAIL_ACCOUNT_ID),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "DELETED"
    finally:
        conn.close()

    flow_state["gmail_delete_done"] = "true"


def test_30_delete_outlook_from_trash(e2e_client, flow_state):
    """Delete 1 Outlook email from trash — verify marked as DELETED."""
    _require(flow_state, "move_to_trash_done")
    outlook_id = flow_state["trash_outlook_ids"][0]

    resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/trash",
        json={
            "action": "delete",
            "items": [{"provider_message_id": outlook_id, "account_id": OUTLOOK_ACCOUNT_ID}],
        },
    )
    _assert_ok(resp)
    assert resp.json()["affected"] == 1

    dsn = os.getenv("DATABASE_URL", "").strip()
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT box FROM email_metadata "
                "WHERE provider_message_id = %s AND account_id = %s",
                (outlook_id, OUTLOOK_ACCOUNT_ID),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "DELETED"
    finally:
        conn.close()

    flow_state["outlook_delete_done"] = "true"


def test_31_restore_outlook_from_trash(e2e_client, flow_state):
    """Restore 1 Outlook email from trash — verify restored (provider_message_id may or may not change)."""
    _require(flow_state, "move_to_trash_done")
    outlook_id = flow_state["trash_outlook_ids"][1]

    dsn = os.getenv("DATABASE_URL", "").strip()

    # Snapshot: count rows in non-TRASH/non-DELETED boxes with cleared previous_box.
    # After a successful restore, this count must grow by exactly 1.
    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM email_metadata "
                "WHERE account_id = %s AND box NOT IN ('TRASH', 'DELETED') "
                "AND previous_box IS NULL",
                (OUTLOOK_ACCOUNT_ID,),
            )
            restored_count_before = cur.fetchone()[0]
    finally:
        conn.close()

    resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/trash",
        json={
            "action": "restore",
            "items": [{"provider_message_id": outlook_id, "account_id": OUTLOOK_ACCOUNT_ID}],
        },
    )
    _assert_ok(resp)
    assert resp.json()["affected"] == 1

    conn = psycopg2.connect(dsn=dsn)
    try:
        with conn.cursor() as cur:
            # Outlook restore may or may not change the provider_message_id.
            cur.execute(
                "SELECT box, previous_box FROM email_metadata "
                "WHERE provider_message_id = %s AND account_id = %s",
                (outlook_id, OUTLOOK_ACCOUNT_ID),
            )
            old_row = cur.fetchone()
            if old_row is not None:
                # ID stayed the same (some moves don't change ID)
                assert old_row[0] != "TRASH", f"Should be restored, got {old_row[0]}"
                assert old_row[1] is None, "previous_box should be NULL after restore"
            else:
                # ID changed — the set of restored rows must have grown by
                # exactly 1. A simple `len(rows) > 0` would pass even if the
                # restore silently failed, because other emails already sit in
                # non-TRASH boxes with NULL previous_box.
                cur.execute(
                    "SELECT COUNT(*) FROM email_metadata "
                    "WHERE account_id = %s AND box NOT IN ('TRASH', 'DELETED') "
                    "AND previous_box IS NULL",
                    (OUTLOOK_ACCOUNT_ID,),
                )
                restored_count_after = cur.fetchone()[0]
                assert restored_count_after == restored_count_before + 1, (
                    f"Expected restored-row count to increase by 1, "
                    f"got {restored_count_before} -> {restored_count_after}"
                )
    finally:
        conn.close()

    flow_state["outlook_restore_done"] = "true"


# ===================================================================
# Section 5b: Drafts — pre-existing accounts (tests 32–33)
#
# Manual DB cleanup is performed inline because these tests verify
# draft creation only. Provider-side draft deletion is tested
# separately in Section 5f (tests 43–44).
# ===================================================================

def test_32_create_draft_gmail(e2e_client):
    """Create a draft on the seeded Gmail test account and clean up the local row."""
    subject = f"E2E draft — Gmail {datetime.now(timezone.utc).isoformat()}"
    response = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": subject,
            "body": "E2E test body",
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["provider_draft_id"]
    assert data["account_id"] == GMAIL_ACCOUNT_ID
    assert data["subject"] == subject
    assert data["to_recipients"] == [SEND_RECIPIENT]
    assert data["cc_recipients"] == []
    assert data["bcc_recipients"] == []
    assert data["body"] == "E2E test body"
    assert data["created_at"]
    assert data["updated_at"]

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (data["provider_draft_id"], GMAIL_ACCOUNT_ID),
            )
            assert cur.fetchone() is not None
            cur.execute(
                "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (data["provider_draft_id"], GMAIL_ACCOUNT_ID),
            )
        conn.commit()
    finally:
        conn.close()


def test_33_create_draft_outlook(e2e_client):
    """Create a draft on the seeded Outlook test account; verifies the ImmutableId header path."""
    subject = f"E2E draft — Outlook {datetime.now(timezone.utc).isoformat()}"
    response = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": subject,
            "body": "E2E Outlook test body",
        },
    )
    _assert_ok(response)
    data = response.json()
    assert data["provider_draft_id"]
    assert data["account_id"] == OUTLOOK_ACCOUNT_ID
    assert data["subject"] == subject
    assert data["to_recipients"] == [SEND_RECIPIENT]
    assert data["cc_recipients"] == []
    assert data["bcc_recipients"] == []
    # Outlook normalises the plain-text body server-side (may add a
    # trailing newline). Match by containment for resilience.
    assert "E2E Outlook test body" in data["body"]
    assert data["created_at"]
    assert data["updated_at"]

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (data["provider_draft_id"], OUTLOOK_ACCOUNT_ID),
            )
            assert cur.fetchone() is not None
            cur.execute(
                "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (data["provider_draft_id"], OUTLOOK_ACCOUNT_ID),
            )
        conn.commit()
    finally:
        conn.close()


# ===================================================================
# Section 5c: Drafts sync (provider → local DB)
#
# Each test creates a draft first (to guarantee the provider has at
# least one known draft with a unique subject), clears the local rows
# for that account, runs the sync, verifies the created draft is now
# in the local DB, and finally cleans up the local rows. Provider-side
# drafts are not deleted (same pattern as tests 32 and 33).
# ===================================================================

def _clear_local_drafts(account_id: str) -> None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM drafts WHERE account_id = %s", (account_id,))
        conn.commit()
    finally:
        conn.close()


def _find_local_draft(provider_draft_id: str, account_id: str) -> tuple | None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, to_recipients, cc_recipients, bcc_recipients, body "
                "FROM drafts "
                "WHERE provider_draft_id = %s AND account_id = %s",
                (provider_draft_id, account_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


def test_34_sync_drafts_gmail_single_account(e2e_client):
    """Gmail + single account: create a draft, sync that account, verify the draft is in DB."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E sync — Gmail single {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "sync test",
        },
    )
    _assert_ok(create_resp)
    created_id = create_resp.json()["provider_draft_id"]

    _clear_local_drafts(GMAIL_ACCOUNT_ID)

    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts/sync?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)
    data = sync_resp.json()
    assert data["total_synced"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == GMAIL_ACCOUNT_ID
    assert data["accounts"][0]["provider"] == "gmail"
    assert data["accounts"][0]["drafts_synced"] == data["total_synced"]
    # The per-account draft fetch is capped at _DRAFTS_MAX_TOTAL (raised to 500).
    assert data["accounts"][0]["drafts_synced"] <= 500

    row = _find_local_draft(created_id, GMAIL_ACCOUNT_ID)
    assert row is not None, "Created draft should be present in DB after sync"
    db_subject, db_to, db_cc, _db_bcc, db_body = row
    assert db_subject == subject
    assert db_to == [SEND_RECIPIENT]
    assert db_cc == []
    assert db_body == "sync test"

    _clear_local_drafts(GMAIL_ACCOUNT_ID)


def test_35_sync_drafts_gmail_mailbox(e2e_client):
    """Gmail + mailbox-wide: create a draft, sync the whole mailbox, verify the draft is in DB."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E sync — Gmail mailbox {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "sync test",
        },
    )
    _assert_ok(create_resp)
    created_id = create_resp.json()["provider_draft_id"]

    _clear_local_drafts(GMAIL_ACCOUNT_ID)

    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts/sync")
    _assert_ok(sync_resp)
    data = sync_resp.json()
    assert data["total_synced"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == GMAIL_ACCOUNT_ID
    assert data["accounts"][0]["provider"] == "gmail"
    assert data["accounts"][0]["drafts_synced"] == data["total_synced"]

    row = _find_local_draft(created_id, GMAIL_ACCOUNT_ID)
    assert row is not None, "Created draft should be present in DB after mailbox sync"
    db_subject, db_to, db_cc, _db_bcc, db_body = row
    assert db_subject == subject
    assert db_to == [SEND_RECIPIENT]
    assert db_cc == []
    assert db_body == "sync test"

    _clear_local_drafts(GMAIL_ACCOUNT_ID)


def test_36_sync_drafts_outlook_single_account(e2e_client):
    """Outlook + single account: create a draft, sync that account, verify the draft is in DB."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E sync — Outlook single {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "sync test",
        },
    )
    _assert_ok(create_resp)
    created_id = create_resp.json()["provider_draft_id"]

    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)

    sync_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/drafts/sync?account_id={OUTLOOK_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)
    data = sync_resp.json()
    assert data["total_synced"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == OUTLOOK_ACCOUNT_ID
    assert data["accounts"][0]["provider"] == "outlook"
    assert data["accounts"][0]["drafts_synced"] == data["total_synced"]

    row = _find_local_draft(created_id, OUTLOOK_ACCOUNT_ID)
    assert row is not None, "Created draft should be present in DB after sync"
    db_subject, db_to, db_cc, _db_bcc, _db_body = row
    assert db_subject == subject
    assert db_to == [SEND_RECIPIENT]
    assert db_cc == []
    # Outlook may normalise the plain-text body server-side (trailing
    # newline / whitespace tweaks); presence-only assertion is enough.

    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)


def test_37_sync_drafts_outlook_mailbox(e2e_client):
    """Outlook + mailbox-wide: create a draft, sync the whole mailbox, verify the draft is in DB."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E sync — Outlook mailbox {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "sync test",
        },
    )
    _assert_ok(create_resp)
    created_id = create_resp.json()["provider_draft_id"]

    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)

    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/drafts/sync")
    _assert_ok(sync_resp)
    data = sync_resp.json()
    assert data["total_synced"] >= 1
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == OUTLOOK_ACCOUNT_ID
    assert data["accounts"][0]["provider"] == "outlook"
    assert data["accounts"][0]["drafts_synced"] == data["total_synced"]

    row = _find_local_draft(created_id, OUTLOOK_ACCOUNT_ID)
    assert row is not None, "Created draft should be present in DB after mailbox sync"
    db_subject, db_to, db_cc, _db_bcc, _db_body = row
    assert db_subject == subject
    assert db_to == [SEND_RECIPIENT]
    assert db_cc == []

    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)


# ===================================================================
# Section 5d: DB-backed GET coverage — pre-existing accounts (tests 38–40)
# ===================================================================
# Tests 38–40 complement the automated suite with end-to-end coverage of
# three GET endpoints that return database content:
#   - GET /mailboxes/{mid}/emails      (list_emails, DB-only)
#   - GET /mailboxes/{mid}/emails/{id}/content (cache-aside)
#   - GET /mailboxes/{mid}/drafts      (list_drafts, DB-only)
# ===================================================================


def test_38_list_emails_gmail(e2e_client):
    """Sync gmail metadata, then GET /emails?box=ALL_MAIL and verify
    the response contains rows for the account."""
    # Ensure there is fresh metadata for the account.
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails"
        f"?box=ALL_MAIL&account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(resp)
    body = resp.json()
    # Paginated envelope (EmailPageOut): items page + exact total.
    emails = body["items"]
    assert isinstance(emails, list)
    assert len(emails) >= 1, "Gmail account should have at least one email in ALL_MAIL after sync"
    # total counts the whole filtered set, so it is at least the page size.
    assert body["total"] >= len(emails)
    # Every returned row must belong to the requested account and box.
    for e in emails:
        assert e["account_id"] == GMAIL_ACCOUNT_ID
        assert e["box"] == "ALL_MAIL"


def test_38a_search_emails_single_account(e2e_client):
    """Search the Gmail account for a generic substring; assert rows respect
    the filter contract (account scope + box + substring on searchable cols)."""
    # Sync metadata so the search has data to work against.
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    # Use a token short enough that almost any inbox can produce a match,
    # but specific enough that pure noise rows should not match.
    needle = "newsletter"
    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": GMAIL_ACCOUNT_ID,
            "q": needle,
        },
    )
    _assert_ok(resp)
    data = resp.json()["items"]
    assert isinstance(data, list)
    # The list may be empty in a hypothetical pristine inbox; what cannot
    # happen is a row that does not match the filter contract.
    needle_lc = needle.lower()
    for e in data:
        assert e["account_id"] == GMAIL_ACCOUNT_ID
        assert e["box"] == "ALL_MAIL"
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert needle_lc in haystack, (
            f"Row {e.get('provider_message_id')} returned by search='{needle}' "
            "does not contain the token in subject / from_email / from_name."
        )


def test_38b_search_emails_unified_mailbox(e2e_client):
    """Search the unified mailbox view (no account_id); assert every row
    belongs to some account in the requested mailbox and matches the token."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata",
    )
    _assert_ok(sync_resp)

    # Look up the set of accounts under the mailbox to validate the scope.
    accounts_resp = e2e_client.get(f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts")
    _assert_ok(accounts_resp)
    mailbox_account_ids = {a["account_id"] for a in accounts_resp.json()}
    assert GMAIL_ACCOUNT_ID in mailbox_account_ids

    needle = "factura"
    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "q": needle},
    )
    _assert_ok(resp)
    data = resp.json()["items"]
    assert isinstance(data, list)
    needle_lc = needle.lower()
    for e in data:
        assert e["account_id"] in mailbox_account_ids
        assert e["box"] == "ALL_MAIL"
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert needle_lc in haystack


def test_38d_search_operators_is_read_state(e2e_client):
    """Gmail-style ``is:`` operator against real data: ``is:unread`` returns
    only unread rows and ``is:read`` only read rows. The two halves are the
    same logical contract (the read-state filter) and stay in one test."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    unread = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "is:unread"},
    )
    _assert_ok(unread)
    for e in unread.json()["items"]:
        assert e["is_read"] is False, (
            f"is:unread returned read row {e.get('provider_message_id')}"
        )

    read = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "is:read"},
    )
    _assert_ok(read)
    for e in read.json()["items"]:
        assert e["is_read"] is True, (
            f"is:read returned unread row {e.get('provider_message_id')}"
        )


def test_38e_search_operator_in_overrides_box(e2e_client):
    """``in:sent`` in q overrides the route box: even with ``box=ALL_MAIL`` every
    returned row is in the SENT box, and ``total`` reflects the overridden box."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    overridden = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "in:sent"},
    )
    _assert_ok(overridden)
    body = overridden.json()
    for e in body["items"]:
        assert e["box"] == "SENT", (
            f"in:sent returned row {e.get('provider_message_id')} in box {e['box']}"
        )
    # The in:sent override must reach the SAME set as querying box=SENT directly:
    # total is computed over the overridden (SENT) box, not the route's ALL_MAIL.
    direct_sent = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "SENT", "account_id": GMAIL_ACCOUNT_ID},
    )
    _assert_ok(direct_sent)
    assert body["total"] == direct_sent.json()["total"]


def test_38f_search_operator_and_combination_reduces_results(e2e_client):
    """Combining an operator with ``is:read`` via AND narrows (never widens) the
    result set, and every row of the combined query matches both predicates."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    base = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "is:read"},
    )
    _assert_ok(base)
    base_total = base.json()["total"]

    combined = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": GMAIL_ACCOUNT_ID,
            "q": "is:read is:unread",
        },
    )
    _assert_ok(combined)
    body = combined.json()
    # is:read AND is:unread is a contradiction → strictly empty, and never more
    # than the is:read-only set.
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total"] <= base_total


def test_38c_pagination_pages_do_not_overlap(e2e_client):
    """Paginate the Gmail ALL_MAIL listing with two adjacent pages and
    verify the envelope contract end-to-end against the real account:
    both responses carry a coherent ``total`` and the two pages share no
    ``provider_message_id`` (OFFSET paging is stable thanks to the total
    ordering tie-break). Skips gracefully when the real account has too
    few emails to fill two pages."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    base = (
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails"
        f"?box=ALL_MAIL&account_id={GMAIL_ACCOUNT_ID}"
    )
    first = e2e_client.get(f"{base}&limit=2&offset=0")
    _assert_ok(first)
    first_body = first.json()
    if first_body["total"] < 3:
        pytest.skip("Gmail account has fewer than 3 ALL_MAIL emails to paginate.")

    second = e2e_client.get(f"{base}&limit=2&offset=2")
    _assert_ok(second)
    second_body = second.json()

    # Both responses are the EmailPageOut envelope with a stable total.
    assert set(first_body.keys()) == {"items", "total", "limit", "offset"}
    assert first_body["total"] == second_body["total"]
    assert first_body["limit"] == 2 and first_body["offset"] == 0
    assert second_body["offset"] == 2

    first_ids = {e["provider_message_id"] for e in first_body["items"]}
    second_ids = {e["provider_message_id"] for e in second_body["items"]}
    assert len(first_body["items"]) == 2
    assert first_ids.isdisjoint(second_ids), (
        "Adjacent pages must not overlap — total ordering tie-break broken."
    )


def test_38g_search_operator_before_after(e2e_client):
    """Date operators ``before:``/``after:`` against the real Gmail account.

    The primary value of this test is proving the runtime carries the IANA
    timezone database (``tzdata``): the parser resolves the date as midnight
    in ``Europe/Madrid`` (real CET/CEST, not a fixed offset), so a deployment
    missing ``tzdata`` would raise at query time and 500 these requests — a
    failure no other test layer can catch (unit/integration share the same
    interpreter but only E2E exercises the deployed runtime + real DB). Fixed
    boundaries keep the assertions deterministic against a live, mutating
    inbox: a far-past ``after:`` lets every row through, while a far-future
    ``after:`` and a far-past ``before:`` must return strictly nothing."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    # after: a date far in the past — every real email qualifies. Assert the
    # per-row contract (received_at on/after the boundary; ISO-8601 strings
    # sort chronologically) and, implicitly, that the request did not 500
    # (which is exactly what a missing tzdata would cause).
    after_past = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "after:2000-01-01"},
    )
    _assert_ok(after_past)
    for e in after_past.json()["items"]:
        assert e["received_at"] >= "2000-01-01", (
            f"after:2000-01-01 returned row {e.get('provider_message_id')} "
            f"with received_at {e['received_at']} before the boundary"
        )

    # after: a date far in the future — nothing qualifies.
    after_future = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "after:2099-01-01"},
    )
    _assert_ok(after_future)
    after_future_body = after_future.json()
    assert after_future_body["items"] == []
    assert after_future_body["total"] == 0

    # before: a date far in the past — nothing qualifies either.
    before_past = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": "before:2000-01-01"},
    )
    _assert_ok(before_past)
    before_past_body = before_past.json()
    assert before_past_body["items"] == []
    assert before_past_body["total"] == 0


def test_38x_list_emails_sort_and_filter(e2e_client):
    """Sort + quick-filter chips against the real Gmail account.

    Sort and chips are properties of the SAME listing endpoint already covered
    above, so per ``common_mistakes.md`` § 1 their assertions stay in one test
    rather than one function per property. The assertions are over invariant
    PROPERTIES (ordering is non-decreasing; every returned row satisfies the
    chip), never over fixed ``provider_message_id`` sets — the live inbox is
    mutable, matching the ``test_38a`` style. The ``has_attachment`` chip is
    deliberately NOT exercised here: ``has_attachments`` is B.lazy (stays FALSE
    until a body is opened), so it is not deterministic at sync time — see the
    note in the backend implementation doc; ``unread`` / ``favorite_only`` are
    DB-faithful right after sync."""
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    # sort=subject&sort_dir=asc: the sequence of normalised subjects must be
    # non-decreasing across the page, and total must be at least the page size.
    sorted_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": GMAIL_ACCOUNT_ID,
            "sort": "subject",
            "sort_dir": "asc",
        },
    )
    _assert_ok(sorted_resp)
    sorted_body = sorted_resp.json()
    subjects = [e.get("subject") or "" for e in sorted_body["items"]]
    # Rank each subject by the backend's actual collation (ask the DB — a
    # Python code-point sort is NOT the same order under en_US.utf8). A
    # correctly ordered ascending page yields a non-decreasing rank sequence.
    ranks = _subject_sort_ranks_via_db(subjects)
    assert ranks == sorted(ranks), (
        "sort=subject&sort_dir=asc did not return rows in non-decreasing "
        "subject order under the database collation."
    )
    assert sorted_body["total"] >= len(sorted_body["items"])

    # unread chip: every returned row is unread.
    unread_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "unread": "true"},
    )
    _assert_ok(unread_resp)
    unread_body = unread_resp.json()
    for e in unread_body["items"]:
        assert e["is_read"] is False, (
            f"unread chip returned read row {e.get('provider_message_id')}"
        )
    assert unread_body["total"] >= len(unread_body["items"])

    # favorite_only chip: every returned row is a favourite.
    fav_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
        params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "favorite_only": "true"},
    )
    _assert_ok(fav_resp)
    fav_body = fav_resp.json()
    for e in fav_body["items"]:
        assert e["is_favorite"] is True, (
            f"favorite_only chip returned non-favourite row {e.get('provider_message_id')}"
        )
    assert fav_body["total"] >= len(fav_body["items"])


def test_39_get_email_content_gmail(e2e_client):
    """Pick an email from the DB, GET /content (first call hits provider
    and caches; second call is a cache hit)."""
    # Sync to guarantee metadata exists.
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    list_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails"
        f"?box=ALL_MAIL&account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(list_resp)
    emails = list_resp.json()["items"]
    if not emails:
        pytest.skip("No Gmail emails available for content fetch test.")

    provider_message_id = emails[0]["provider_message_id"]
    content_url = (
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{provider_message_id}/content"
        f"?account_id={GMAIL_ACCOUNT_ID}"
    )

    # First call: cache miss → provider fetch.
    first = e2e_client.get(content_url)
    _assert_ok(first)
    body_1 = first.json()
    assert "html_body" in body_1 or "text_body" in body_1

    # Second call: cache hit → same response.
    second = e2e_client.get(content_url)
    _assert_ok(second)
    body_2 = second.json()
    assert body_2 == body_1


def test_40_list_drafts_gmail(e2e_client):
    """Create a gmail draft, verify GET /drafts?account_id returns it, cleanup."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E list — Gmail {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "list test",
        },
    )
    _assert_ok(create_resp)
    created_id = create_resp.json()["provider_draft_id"]
    try:
        resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts?account_id={GMAIL_ACCOUNT_ID}",
        )
        _assert_ok(resp)
        drafts = resp.json()
        assert isinstance(drafts, list)
        matched = [d for d in drafts if d["provider_draft_id"] == created_id]
        assert matched, (
            f"Created draft {created_id} should appear in GET /drafts result."
        )
        assert matched[0]["subject"] == subject
        assert matched[0]["account_id"] == GMAIL_ACCOUNT_ID
    finally:
        # Cleanup the local row (draft remains at the provider, same pattern
        # as tests 32–37).
        _clear_local_drafts(GMAIL_ACCOUNT_ID)


# ===================================================================
# Section 5e: Drafts update (tests 41–42)
#
# Each test creates a draft via POST, captures the returned
# provider_draft_id, calls the PATCH endpoint with new recipients /
# subject / body, asserts the response reflects the new content,
# verifies the local DB row, and cleans up (deletes the local row only;
# the draft is intentionally left at the provider — same pattern as
# tests 32/33).
# ===================================================================


def test_41_update_draft_gmail(e2e_client):
    """Create a Gmail draft, update it via PATCH, verify the new content, clean up."""
    ts = datetime.now(timezone.utc).isoformat()
    initial_subject = f"E2E update — Gmail {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": initial_subject,
            "body": "E2E update initial",
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]
    original_created_at = create_resp.json()["created_at"]

    try:
        new_subject = f"E2E updated — Gmail {datetime.now(timezone.utc).isoformat()}"
        patch_resp = e2e_client.patch(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}",
            json={
                "to_recipients": [SEND_RECIPIENT],
                "cc_recipients": [],
                "bcc_recipients": [],
                "subject": new_subject,
                "body": "E2E updated body",
            },
        )
        _assert_ok(patch_resp)
        data = patch_resp.json()
        assert data["provider_draft_id"] == provider_draft_id
        assert data["account_id"] == GMAIL_ACCOUNT_ID
        assert data["to_recipients"] == [SEND_RECIPIENT]
        assert data["cc_recipients"] == []
        assert data["bcc_recipients"] == []
        assert data["subject"] == new_subject
        assert data["body"] == "E2E updated body"
        assert data["created_at"] == original_created_at
        assert data["updated_at"] >= original_created_at

        # Verify the local DB row reflects the new content.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT subject, body, to_recipients, created_at, updated_at "
                    "FROM drafts "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                row = cur.fetchone()
            assert row is not None
            assert row[0] == new_subject
            assert row[1] == "E2E updated body"
            assert row[2] == [SEND_RECIPIENT]
        finally:
            conn.close()
    finally:
        # Clean up the local row (draft stays at the provider — same as tests 32/33).
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
            conn.commit()
        finally:
            conn.close()


def test_42_update_draft_outlook(e2e_client):
    """Create an Outlook draft, update it via PATCH, verify the new content, clean up."""
    ts = datetime.now(timezone.utc).isoformat()
    initial_subject = f"E2E update — Outlook {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": initial_subject,
            "body": "E2E update initial",
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]
    original_created_at = create_resp.json()["created_at"]

    try:
        new_subject = f"E2E updated — Outlook {datetime.now(timezone.utc).isoformat()}"
        patch_resp = e2e_client.patch(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}",
            json={
                "to_recipients": [SEND_RECIPIENT],
                "cc_recipients": [],
                "bcc_recipients": [],
                "subject": new_subject,
                "body": "E2E updated body",
            },
        )
        _assert_ok(patch_resp)
        data = patch_resp.json()
        assert data["provider_draft_id"] == provider_draft_id
        assert data["account_id"] == OUTLOOK_ACCOUNT_ID
        assert data["to_recipients"] == [SEND_RECIPIENT]
        assert data["cc_recipients"] == []
        assert data["bcc_recipients"] == []
        assert data["subject"] == new_subject
        # Outlook may normalise the plain-text body server-side
        # (trailing newline / whitespace tweaks) so match by containment.
        assert "E2E updated body" in data["body"]
        assert data["created_at"] == original_created_at
        assert data["updated_at"] >= original_created_at

        # Verify the local DB row reflects the new content.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT subject, body, to_recipients "
                    "FROM drafts "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                row = cur.fetchone()
            assert row is not None
            assert row[0] == new_subject
            assert "E2E updated body" in row[1]
            assert row[2] == [SEND_RECIPIENT]
        finally:
            conn.close()
    finally:
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
            conn.commit()
        finally:
            conn.close()


# ===================================================================
# Section 5f: Delete draft — Provider-First (tests 43–44)
#
# Each test creates a draft, deletes it via the endpoint, and verifies
# the draft is gone both from the local DB and from the provider
# (via a sync reconciliation).
# ===================================================================


def test_43_delete_draft_gmail(e2e_client):
    """Create a Gmail draft, delete it via the endpoint, verify it's gone
    from local DB AND from the Gmail provider (via sync reconciliation)."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E delete — Gmail {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "delete test",
        },
    )
    _assert_ok(create_resp)
    draft_id = create_resp.json()["provider_draft_id"]

    delete_resp = e2e_client.delete(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts/{draft_id}",
    )
    _assert_ok(delete_resp)
    assert delete_resp.json() == {"status": "deleted"}

    # Local-DB assertion: row is gone.
    assert _find_local_draft(draft_id, GMAIL_ACCOUNT_ID) is None

    # Provider assertion: wipe local rows and re-sync — the deleted draft
    # must NOT reappear, which proves it was deleted at Gmail.
    _clear_local_drafts(GMAIL_ACCOUNT_ID)
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts/sync?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)
    assert _find_local_draft(draft_id, GMAIL_ACCOUNT_ID) is None

    _clear_local_drafts(GMAIL_ACCOUNT_ID)


def test_44_delete_draft_outlook(e2e_client):
    """Create an Outlook draft, delete it via the endpoint, verify it's gone
    from local DB AND from the Outlook provider (via sync reconciliation)."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E delete — Outlook {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "subject": subject,
            "body": "delete test",
        },
    )
    _assert_ok(create_resp)
    draft_id = create_resp.json()["provider_draft_id"]

    delete_resp = e2e_client.delete(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts/{draft_id}",
    )
    _assert_ok(delete_resp)
    assert delete_resp.json() == {"status": "deleted"}

    # Local-DB assertion: row is gone.
    assert _find_local_draft(draft_id, OUTLOOK_ACCOUNT_ID) is None

    # Provider assertion: wipe local rows and re-sync — the deleted draft
    # must NOT reappear, which proves it was deleted at Outlook.
    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)
    sync_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/drafts/sync?account_id={OUTLOOK_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)
    assert _find_local_draft(draft_id, OUTLOOK_ACCOUNT_ID) is None

    _clear_local_drafts(OUTLOOK_ACCOUNT_ID)


# ===================================================================
# Section 5g: Send draft (pre-existing connected accounts, tests 45–46)
#
# Each test creates a draft first, sends it via the POST .../send
# endpoint, verifies the response and that the local draft row was
# deleted. A finally block ensures orphan draft rows are cleaned up.
# ===================================================================

def test_45_send_draft_gmail(e2e_client):
    """Create a Gmail draft, send it, verify response and DB cleanup."""
    subject = f"E2E send draft — Gmail {datetime.now(timezone.utc).isoformat()}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": subject,
            "body": "E2E send draft test body",
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        send_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        data = send_resp.json()
        assert data["status"] == "sent"
        assert data["provider_message_id"]
        assert data["provider_message_id"] != provider_draft_id  # Gmail returns new ID
        assert data["provider"] == "gmail"

        # Verify the draft row was deleted from the local DB
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone() is None, "Draft row should be deleted after send"
        finally:
            conn.close()
    finally:
        # Safety-net cleanup: delete any orphan draft row if the send failed
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
            conn.commit()
        finally:
            conn.close()


def test_46_send_draft_outlook(e2e_client):
    """Create an Outlook draft, send it, verify response and DB cleanup."""
    subject = f"E2E send draft — Outlook {datetime.now(timezone.utc).isoformat()}"
    create_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": subject,
            "body": "E2E send draft Outlook test body",
        },
    )
    _assert_ok(create_resp)
    provider_draft_id = create_resp.json()["provider_draft_id"]

    try:
        send_resp = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        data = send_resp.json()
        assert data["status"] == "sent"
        assert data["provider_message_id"]
        assert data["provider_message_id"] == provider_draft_id  # Outlook ImmutableId
        assert data["provider"] == "outlook"

        # Verify the draft row was deleted from the local DB
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone() is None, "Draft row should be deleted after send"
        finally:
            conn.close()
    finally:
        # Safety-net cleanup: delete any orphan draft row if the send failed
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
            conn.commit()
        finally:
            conn.close()


# ===================================================================
# Section 5b: Email content (cache-aside: MISS populates DB, HIT serves from DB)
# ===================================================================

def _assert_content_payload(data: dict) -> None:
    assert "html_body" in data and "text_body" in data
    assert data["html_body"] is not None or data["text_body"] is not None
    if data["html_body"] is not None:
        lower = data["html_body"].lower()
        assert "<script" not in lower
        assert "javascript:" not in lower


def test_46a_email_content_gmail_miss(e2e_client, flow_state):
    """Cache MISS: empty email_content → endpoint fetches from provider + persists.

    The ``_delete_email_content`` below is now LOAD-BEARING: ``sync-metadata``
    runs a background content prefetch for recent unread inbox mail, so it may
    pre-cache ``msg_id`` seconds after responding. Deleting the row right after
    the sync neutralises that prefetch and restores a genuine MISS for the GET.
    """
    sync_resp = e2e_client.post(f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_id = _fetch_one_message_id(GMAIL_ACCOUNT_ID)
    if msg_id is None:
        pytest.skip("No synced emails found for Gmail test account")

    _delete_email_content(GMAIL_ACCOUNT_ID, msg_id)
    assert _fetch_email_content_row(GMAIL_ACCOUNT_ID, msg_id) is None

    response = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": GMAIL_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    _assert_content_payload(data)

    row = _fetch_email_content_row(GMAIL_ACCOUNT_ID, msg_id)
    assert row is not None
    assert row[0] == data["html_body"]
    assert row[1] == data["text_body"]

    # Follow-up (same test, per common_mistakes #1): a second GET now resolves
    # from the just-persisted cache and returns the identical payload — the
    # immediate cache-hit side effect of the MISS we just exercised.
    second = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": GMAIL_ACCOUNT_ID},
    )
    _assert_ok(second)
    assert second.json() == data

    flow_state["gmail_content_msg_id"] = msg_id
    flow_state["gmail_content_fetched_at"] = row[2].isoformat()
    flow_state["gmail_content_payload"] = data


def test_46b_email_content_gmail_hit(e2e_client, flow_state):
    """Cache HIT: row already persisted → endpoint returns it without re-inserting."""
    _require(flow_state, "gmail_content_msg_id", "gmail_content_fetched_at")
    msg_id = flow_state["gmail_content_msg_id"]
    prev_fetched_at = flow_state["gmail_content_fetched_at"]
    prev_payload = flow_state["gmail_content_payload"]

    response = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": GMAIL_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    assert data == prev_payload

    row = _fetch_email_content_row(GMAIL_ACCOUNT_ID, msg_id)
    assert row is not None
    assert row[2].isoformat() == prev_fetched_at


def test_46c_email_content_outlook_miss(e2e_client, flow_state):
    # The post-sync ``_delete_email_content`` is LOAD-BEARING for the same
    # reason as test_46a: the sync's background content prefetch may pre-cache
    # ``msg_id``, and the delete restores a genuine MISS for the GET below.
    sync_resp = e2e_client.post(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata")
    _assert_ok(sync_resp)

    msg_id = _fetch_one_message_id(OUTLOOK_ACCOUNT_ID)
    if msg_id is None:
        pytest.skip("No synced emails found for Outlook test account")

    _delete_email_content(OUTLOOK_ACCOUNT_ID, msg_id)
    assert _fetch_email_content_row(OUTLOOK_ACCOUNT_ID, msg_id) is None

    response = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": OUTLOOK_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    _assert_content_payload(data)

    row = _fetch_email_content_row(OUTLOOK_ACCOUNT_ID, msg_id)
    assert row is not None
    assert row[0] == data["html_body"]
    assert row[1] == data["text_body"]

    # Follow-up (same test, per common_mistakes #1): a second GET resolves from
    # the just-persisted cache and returns the identical payload.
    second = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": OUTLOOK_ACCOUNT_ID},
    )
    _assert_ok(second)
    assert second.json() == data

    flow_state["outlook_content_msg_id"] = msg_id
    flow_state["outlook_content_fetched_at"] = row[2].isoformat()
    flow_state["outlook_content_payload"] = data


def test_46d_email_content_outlook_hit(e2e_client, flow_state):
    _require(flow_state, "outlook_content_msg_id", "outlook_content_fetched_at")
    msg_id = flow_state["outlook_content_msg_id"]
    prev_fetched_at = flow_state["outlook_content_fetched_at"]
    prev_payload = flow_state["outlook_content_payload"]

    response = e2e_client.get(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/{msg_id}/content",
        params={"account_id": OUTLOOK_ACCOUNT_ID},
    )
    _assert_ok(response)
    data = response.json()
    assert data == prev_payload

    row = _fetch_email_content_row(OUTLOOK_ACCOUNT_ID, msg_id)
    assert row is not None
    assert row[2].isoformat() == prev_fetched_at


# ===================================================================
# Section 5h: Draft attachments — local-only (D-07) end-to-end against
# the real Gmail and Outlook test accounts.
#
# Each test creates a draft, drives the attachment lifecycle (POST →
# GET-via-list → DELETE → 404 follow-up) in the SAME test (per
# common_mistakes.md §1: simple follow-up assertions live with the
# destructive action), and finally cleans up the local draft row. The
# attachment endpoints never touch the provider so a Gmail and an
# Outlook variant exercise the same code path; one of each is enough
# coverage for the lifecycle, while the send-with-attachment tests
# below exercise the per-provider upload strategies.
# ===================================================================


def _create_local_draft(e2e_client, mailbox_id: str, account_id: str, suffix: str) -> str:
    """Create a draft used as the parent of attachment lifecycle tests.

    Returns its ``provider_draft_id``. Caller is responsible for the
    final ``DELETE FROM drafts`` cleanup in a ``finally`` block.
    """
    ts = datetime.now(timezone.utc).isoformat()
    response = e2e_client.post(
        f"/mailboxes/{mailbox_id}/accounts/{account_id}/drafts",
        json={
            "to_recipients": [SEND_RECIPIENT],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": f"E2E attachments — {suffix} {ts}",
            "body": f"E2E attachments parent draft {suffix}",
        },
    )
    _assert_ok(response)
    return response.json()["provider_draft_id"]


def _delete_draft_row_locally(provider_draft_id: str, account_id: str) -> None:
    """Best-effort cleanup of a draft row left over by a test failure."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                (provider_draft_id, account_id),
            )
        conn.commit()
    finally:
        conn.close()


def test_46e_draft_attachment_lifecycle_gmail(e2e_client):
    """POST → list (via draft GET) → DELETE → 404 follow-up on Gmail."""
    provider_draft_id = _create_local_draft(
        e2e_client, GMAIL_MAILBOX_ID, GMAIL_ACCOUNT_ID, "Gmail",
    )
    try:
        upload = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments",
            files={
                "file": (
                    "report.pdf",
                    b"%PDF-1.4 fake pdf bytes for E2E",
                    "application/pdf",
                ),
            },
        )
        _assert_ok(upload, expected=201)
        body = upload.json()
        attachment_id = body["draft_attachment_id"]
        assert body["filename"] == "report.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["size"] > 0
        assert body["position"] == 0
        # D-07: provider untouched until send.
        assert body["provider_attachment_id"] is None

        list_resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/drafts?account_id={GMAIL_ACCOUNT_ID}",
        )
        _assert_ok(list_resp)
        matching = [
            d for d in list_resp.json()
            if d["provider_draft_id"] == provider_draft_id
        ]
        assert matching, "Parent draft should be visible in GET /drafts"
        attachments = matching[0].get("attachments") or []
        assert any(
            a["draft_attachment_id"] == attachment_id for a in attachments
        ), "Uploaded attachment should appear in the draft's attachments list"

        # Destructive action + follow-up 404 — common_mistakes.md §1.
        delete_resp = e2e_client.delete(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/{attachment_id}",
        )
        _assert_ok(delete_resp)
        assert delete_resp.json() == {"status": "deleted"}

        followup = e2e_client.delete(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/{attachment_id}",
        )
        _assert_ok(followup, expected=404)
        assert followup.json()["error"]["code"] == "draft_attachment_not_found"
    finally:
        _delete_draft_row_locally(provider_draft_id, GMAIL_ACCOUNT_ID)


def test_46f_draft_attachment_lifecycle_outlook(e2e_client):
    """Same flow on Outlook — verifies the local-only path is provider-agnostic."""
    provider_draft_id = _create_local_draft(
        e2e_client, OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID, "Outlook",
    )
    try:
        upload = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments",
            files={
                "file": (
                    "notes.txt",
                    b"E2E attachment payload - Outlook lifecycle test",
                    "text/plain",
                ),
            },
        )
        _assert_ok(upload, expected=201)
        body = upload.json()
        attachment_id = body["draft_attachment_id"]
        assert body["filename"] == "notes.txt"
        assert body["mime_type"] == "text/plain"
        assert body["provider_attachment_id"] is None

        delete_resp = e2e_client.delete(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/{attachment_id}",
        )
        _assert_ok(delete_resp)
        assert delete_resp.json() == {"status": "deleted"}

        followup = e2e_client.delete(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments/{attachment_id}",
        )
        _assert_ok(followup, expected=404)
        assert followup.json()["error"]["code"] == "draft_attachment_not_found"
    finally:
        _delete_draft_row_locally(provider_draft_id, OUTLOOK_ACCOUNT_ID)


def test_46g_draft_attachment_blocked_extension_rejected(e2e_client):
    """The 400 ``attachment_blocked_extension`` envelope reaches a real client."""
    provider_draft_id = _create_local_draft(
        e2e_client, GMAIL_MAILBOX_ID, GMAIL_ACCOUNT_ID, "Blocked",
    )
    try:
        response = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments",
            files={
                "file": (
                    "malware.exe",
                    b"this is not a real exe - the extension is enough",
                    "application/octet-stream",
                ),
            },
        )
        _assert_ok(response, expected=400)
        assert response.json()["error"]["code"] == "attachment_blocked_extension"
    finally:
        _delete_draft_row_locally(provider_draft_id, GMAIL_ACCOUNT_ID)


def test_46h_send_draft_with_attachment_gmail(e2e_client):
    """Send a Gmail draft with one PDF attachment end-to-end (D-18 SIMPLE strategy)."""
    provider_draft_id = _create_local_draft(
        e2e_client, GMAIL_MAILBOX_ID, GMAIL_ACCOUNT_ID, "Gmail send w/ attachment",
    )
    try:
        upload = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments",
            files={
                "file": (
                    "e2e-attachment.pdf",
                    b"%PDF-1.4 e2e test bytes for send-with-attachment",
                    "application/pdf",
                ),
            },
        )
        _assert_ok(upload, expected=201)

        send_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        data = send_resp.json()
        assert data["status"] == "sent"
        assert data["provider"] == "gmail"
        assert data["provider_message_id"]
        assert data["provider_message_id"] != provider_draft_id

        # Provider-First + CASCADE delete: the local draft row and any
        # draft_attachments must be gone after a successful send.
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
                cur.execute(
                    "SELECT COUNT(*) FROM draft_attachments "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, GMAIL_ACCOUNT_ID),
                )
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        _delete_draft_row_locally(provider_draft_id, GMAIL_ACCOUNT_ID)


def test_46i_send_draft_with_attachment_outlook(e2e_client):
    """Send an Outlook draft with one small attachment (D-18 SIMPLE method-B)."""
    provider_draft_id = _create_local_draft(
        e2e_client, OUTLOOK_MAILBOX_ID, OUTLOOK_ACCOUNT_ID,
        "Outlook send w/ attachment",
    )
    try:
        upload = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/attachments",
            files={
                "file": (
                    "e2e-outlook.txt",
                    b"E2E Outlook send-with-attachment payload bytes",
                    "text/plain",
                ),
            },
        )
        _assert_ok(upload, expected=201)

        send_resp = e2e_client.post(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts/{OUTLOOK_ACCOUNT_ID}"
            f"/drafts/{provider_draft_id}/send",
        )
        _assert_ok(send_resp)
        data = send_resp.json()
        assert data["status"] == "sent"
        assert data["provider"] == "outlook"
        # Outlook keeps the draft id thanks to ImmutableId.
        assert data["provider_message_id"] == provider_draft_id

        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM drafts WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone() is None
                cur.execute(
                    "SELECT COUNT(*) FROM draft_attachments "
                    "WHERE provider_draft_id = %s AND account_id = %s",
                    (provider_draft_id, OUTLOOK_ACCOUNT_ID),
                )
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        _delete_draft_row_locally(provider_draft_id, OUTLOOK_ACCOUNT_ID)


def test_46j_admin_purge_disabled_when_env_unset(e2e_client, monkeypatch):
    """When ``ATTACHMENTS_PURGE_TOKEN`` is unset, /admin/attachments/purge is 503."""
    # The fixture-level env var is not configured by default — the env
    # may or may not have it. To make the assertion deterministic we
    # actively unset it for the duration of this test.
    monkeypatch.delenv("ATTACHMENTS_PURGE_TOKEN", raising=False)
    response = e2e_client.post(
        "/admin/attachments/purge",
        headers={"X-Admin-Token": "anything"},
    )
    _assert_ok(response, expected=503)
    assert response.json()["error"]["code"] == "purge_disabled"


def test_46k_admin_purge_invalid_token_when_env_set(e2e_client, monkeypatch):
    """With env set + wrong header → 401 ``invalid_admin_token``."""
    monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token-value")
    response = e2e_client.post(
        "/admin/attachments/purge",
        headers={"X-Admin-Token": "wrong-token"},
    )
    _assert_ok(response, expected=401)
    assert response.json()["error"]["code"] == "invalid_admin_token"


def test_46l_admin_purge_runs_when_token_matches(e2e_client, monkeypatch):
    """With env set + correct header → 200 with purge stats payload."""
    monkeypatch.setenv("ATTACHMENTS_PURGE_TOKEN", "expected-token-value")
    response = e2e_client.post(
        "/admin/attachments/purge",
        headers={"X-Admin-Token": "expected-token-value"},
    )
    _assert_ok(response)
    data = response.json()
    assert "purged_count" in data
    assert "freed_bytes" in data
    assert isinstance(data["purged_count"], int)
    assert isinstance(data["freed_bytes"], int)
    assert data["purged_count"] >= 0
    assert data["freed_bytes"] >= 0


def _fetch_downloadable_attachment(account_id: str, provider_message_id: str):
    """Return ``(attachment_id, filename, mime_type)`` of the first
    non-inline attachment row for the message, or ``None``.
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT attachment_id, filename, mime_type "
                "FROM email_attachments "
                "WHERE account_id = %s AND provider_message_id = %s "
                "AND is_inline = FALSE "
                "ORDER BY position LIMIT 1",
                (account_id, provider_message_id),
            )
            row = cur.fetchone()
            return (row[0], row[1], row[2]) if row else None
    finally:
        conn.close()


def test_46m_download_received_attachment_gmail(e2e_client):
    """Download the binary of a RECEIVED Gmail attachment end-to-end (D-06).

    Flow: ensure a message with a downloadable attachment exists (bootstrap
    a self-addressed PDF if needed) → prime ``GET /content`` so the
    attachment row is discovered/persisted → ``GET`` the download endpoint
    and assert the response carries the real name+extension on
    ``Content-Disposition`` and the resolved mime on ``Content-Type``.

    The header verification lives in the SAME test as the download call
    (common_mistakes.md §1 — it is the side effect of the endpoint under
    test, not a separate behaviour).
    """
    candidate_pmid = bootstrap_attachment_message(
        e2e_client, GMAIL_MAILBOX_ID, GMAIL_ACCOUNT_ID,
    )
    if candidate_pmid is None:
        pytest.skip("No Gmail message with a downloadable attachment available")

    # Prime the cache-aside content endpoint so attachments are classified
    # and persisted into email_attachments.
    content_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/{candidate_pmid}/content"
        f"?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(content_resp)

    row = _fetch_downloadable_attachment(GMAIL_ACCOUNT_ID, candidate_pmid)
    if row is None:
        pytest.skip("Content fetch produced no downloadable attachment row")
    attachment_id, filename, mime_type = row

    download = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}"
        f"/emails/{candidate_pmid}/attachments/{attachment_id}",
    )
    _assert_ok(download)
    # The binary is non-empty and the headers carry the real name + mime.
    assert len(download.content) > 0
    disposition = download.headers["content-disposition"]
    assert f'filename="{filename}"' in disposition
    assert download.headers["content-type"].startswith(mime_type)
    assert download.headers["x-content-type-options"] == "nosniff"


def test_46n_contact_suggestions(e2e_client):
    """Recipient autocomplete against the real synced mailboxes (DB-only,
    no provider call). Mirrors the search tests (test_38a/test_38b): assert
    by CONTAINMENT — every returned suggestion's email or name contains the
    fragment — never by count or order (the real mailbox contents vary).

    The ``q`` < 2 → 422 contract check rides the SAME test (common_mistakes
    §1): it verifies a boundary of the very endpoint under test, not a
    separate behaviour.
    """
    # Sync so the aggregation has the user's mail to draw from. The needle
    # is derived from SEND_RECIPIENT (the address every send/draft test
    # targets), so the SENT rows of the test account are very likely to
    # carry it on ``to_email`` — but the assertion tolerates an empty list
    # the same way the search tests tolerate a pristine inbox.
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    local_part = SEND_RECIPIENT.split("@", 1)[0]
    needle = local_part[:5] if len(local_part) >= 5 else local_part
    assert len(needle) >= 2, "SEND_RECIPIENT local part too short to form a fragment"

    resp = e2e_client.get("/contacts/suggestions", params={"q": needle})
    _assert_ok(resp)
    data = resp.json()
    assert isinstance(data, list)
    needle_lc = needle.lower()
    for item in data:
        assert set(item.keys()) == {"email", "name"}
        haystack = " ".join([
            (item.get("email") or ""),
            (item.get("name") or ""),
        ]).lower()
        assert needle_lc in haystack, (
            f"Suggestion {item.get('email')} returned for q='{needle}' does not "
            "contain the fragment in its email or name."
        )

    # Contract: a single-character fragment is rejected at the router.
    too_short = e2e_client.get("/contacts/suggestions", params={"q": "a"})
    _assert_ok(too_short, expected=422)


def test_46o_unread_count(e2e_client):
    """Unread-count badge against the real synced Gmail mailbox (DB-only, no
    provider call). Verifies the count matches a direct SQL control query for
    ALL_MAIL and SPAM, then proves it tracks a real read-state mutation:
    marking one unread ALL_MAIL message as read drops ``total`` by one.

    All follow-up assertions stay in this single flow test (common_mistakes
    §1 — they verify side effects of the very endpoint under test, not
    separate behaviours). The read mutation is restored at the end so the
    seeded account looks identical before and after (pre-existing test data
    is sacred).
    """
    # Sync so email_metadata reflects the provider before we count.
    sync_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    # ALL_MAIL: total + the account's breakdown entry both match the DB.
    expected_allmail = _count_unread_by_box(GMAIL_ACCOUNT_ID, "ALL_MAIL")
    resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/unread-count",
        params={"box": "ALL_MAIL"},
    )
    _assert_ok(resp)
    body = resp.json()
    assert body["box"] == "ALL_MAIL"
    assert body["total"] == expected_allmail
    entry = next(a for a in body["accounts"] if a["account_id"] == GMAIL_ACCOUNT_ID)
    assert entry["unread"] == expected_allmail
    # total is summed in Python from the per-account breakdown (coherence check).
    assert body["total"] == sum(a["unread"] for a in body["accounts"])

    # SPAM: same contract against its own DB control count.
    expected_spam = _count_unread_by_box(GMAIL_ACCOUNT_ID, "SPAM")
    spam_resp = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/unread-count",
        params={"box": "SPAM"},
    )
    _assert_ok(spam_resp)
    assert spam_resp.json()["total"] == expected_spam

    # Invalid box is rejected by the router Literal (422) — exercised against
    # the real runtime, mirroring test_46n's q<2 boundary assertion.
    bad_box = e2e_client.get(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/unread-count",
        params={"box": "TRASH"},
    )
    assert bad_box.status_code == 422

    # Track a real Provider-First mutation: pick one unread ALL_MAIL message,
    # mark it read, and assert the badge total decrements by exactly one.
    unread_id = None
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id FROM email_metadata "
                "WHERE account_id = %s AND box = 'ALL_MAIL' AND is_read = FALSE LIMIT 1",
                (GMAIL_ACCOUNT_ID,),
            )
            row = cur.fetchone()
            unread_id = row[0] if row else None
    finally:
        conn.close()

    if unread_id is None:
        pytest.skip("No unread ALL_MAIL message available to exercise the decrement.")

    mark_read = e2e_client.patch(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/read-status",
        json={
            "is_read": True,
            "items": [{"account_id": GMAIL_ACCOUNT_ID, "provider_message_id": unread_id}],
        },
    )
    _assert_ok(mark_read)
    try:
        after = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/unread-count",
            params={"box": "ALL_MAIL"},
        )
        _assert_ok(after)
        assert after.json()["total"] == expected_allmail - 1
    finally:
        # Restore the message to unread so the seeded account is unchanged.
        e2e_client.patch(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/read-status",
            json={
                "is_read": False,
                "items": [{"account_id": GMAIL_ACCOUNT_ID, "provider_message_id": unread_id}],
            },
        )


def test_46p_unread_count_outlook_and_full_breakdown(e2e_client):
    """Outlook counterpart of test_46o. The Gmail-only test could not exercise
    the Outlook provider path nor the "every account present" invariant, since
    each seeded mailbox holds a single account. Verifies the count against a DB
    control query for ALL_MAIL and SPAM, the ``total == sum(breakdown)``
    coherence, and that the breakdown lists an entry for EVERY account of the
    mailbox — including any the GROUP BY omits (0-filled), never just the
    accounts that have unread rows.
    """
    sync_resp = e2e_client.post(
        f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/sync-metadata?account_id={OUTLOOK_ACCOUNT_ID}",
    )
    _assert_ok(sync_resp)

    accounts_resp = e2e_client.get(f"/mailboxes/{OUTLOOK_MAILBOX_ID}/accounts")
    _assert_ok(accounts_resp)
    mailbox_account_ids = {a["account_id"] for a in accounts_resp.json()}

    for box in ("ALL_MAIL", "SPAM"):
        expected = _count_unread_by_box(OUTLOOK_ACCOUNT_ID, box)
        resp = e2e_client.get(
            f"/mailboxes/{OUTLOOK_MAILBOX_ID}/emails/unread-count",
            params={"box": box},
        )
        _assert_ok(resp)
        body = resp.json()
        assert body["box"] == box
        # total is summed in Python from the per-account breakdown.
        assert body["total"] == sum(a["unread"] for a in body["accounts"])
        # Every owned account appears in the breakdown (0-filled if it has no
        # unread rows) — the breakdown is never a subset of the mailbox.
        assert {a["account_id"] for a in body["accounts"]} == mailbox_account_ids
        entry = next(a for a in body["accounts"] if a["account_id"] == OUTLOOK_ACCOUNT_ID)
        assert entry["unread"] == expected


# ===================================================================
# Section 5x: Backfill status — background bulk-load progress
# ===================================================================
# The interactive OAuth connect that enqueues a backfill is excluded from E2E,
# and a real 100k backfill runs for hours, so the loop itself is covered by the
# unit + integration suites. Here we only verify the read endpoint's contract
# against a pre-existing account: it already has a sync_cursor and no job, so it
# reports no active backfill. This also protects that mounting the endpoint on
# the real mailboxes_router (global rate-limit only, no provider_sync bucket)
# works end-to-end.


def test_46q_backfill_status_no_active_backfill(e2e_client):
    resp = e2e_client.get(f"/mailboxes/{GMAIL_MAILBOX_ID}/backfill-status")
    _assert_ok(resp)
    data = resp.json()
    assert isinstance(data["accounts"], list)
    # A long-synced test account is never mid-backfill: nothing pending/running.
    assert data["active"] is False
    # Defensive per-entry guard: accounts is normally empty here (a long-synced
    # account has no job); the per-account contract is covered in unit+integration.
    for entry in data["accounts"]:
        assert entry["status"] in ("completed", "failed")
        assert entry["done"] is True


def test_46r_account_quota(e2e_client):
    """GET /accounts/quota is DB-only: it counts every account the seeded user
    owns across all their mailboxes (Gmail + Outlook) against the configured
    cap. Read-only — no provider call, no mutation."""
    resp = e2e_client.get("/accounts/quota")
    _assert_ok(resp)
    data = resp.json()
    # The seeded E2E user owns at least the Gmail and Outlook test accounts.
    assert data["connected"] >= 2
    assert data["limit"] >= 1
    # Usage never exceeds the cap on a healthy seeded account set.
    assert data["connected"] <= data["limit"]


def test_46s_account_limit_exceeded(e2e_client, monkeypatch):
    """POST /mailboxes/{id}/accounts returns 409 account_limit_exceeded once the
    per-user cap is reached. Forced deterministically by lowering
    MAX_ACCOUNTS_PER_USER below the seeded account count; the guard fires BEFORE
    any INSERT, so no account row is created (the sacred seeded data is
    untouched)."""
    monkeypatch.setenv("MAX_ACCOUNTS_PER_USER", "1")
    resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts",
        json={"provider": "gmail", "display_label": "e2e-limit-probe"},
    )
    _assert_ok(resp, expected=409)
    error = resp.json()["error"]
    assert error["code"] == "account_limit_exceeded"
    # The 409 detail carries the numbers the frontend counter reads.
    assert error["detail"]["limit"] == 1
    assert error["detail"]["connected"] >= 1


def test_46t_drafts_excluded_from_email_metadata_gmail(e2e_client):
    """A Gmail draft must never leak into email_metadata: after a metadata sync
    the draft's unique subject is absent from the /emails listing — proving the
    ``-in:drafts`` query filter plus the ``DRAFT`` labelIds guard end-to-end. The
    provider draft is deleted and local draft rows cleared in a finally."""
    ts = datetime.now(timezone.utc).isoformat()
    subject = f"E2E draft-exclusion {ts}"
    create_resp = e2e_client.post(
        f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts",
        json={"to_recipients": [SEND_RECIPIENT], "subject": subject, "body": "draft-exclusion"},
    )
    _assert_ok(create_resp)
    draft_id = create_resp.json()["provider_draft_id"]
    try:
        sync_resp = e2e_client.post(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails/sync-metadata?account_id={GMAIL_ACCOUNT_ID}",
        )
        _assert_ok(sync_resp)
        list_resp = e2e_client.get(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/emails",
            params={"box": "ALL_MAIL", "account_id": GMAIL_ACCOUNT_ID, "q": subject},
        )
        _assert_ok(list_resp)
        items = list_resp.json()["items"]
        # The draft's subject must not surface — drafts are kept out of email_metadata.
        assert all(subject not in (it.get("subject") or "") for it in items)
    finally:
        e2e_client.delete(
            f"/mailboxes/{GMAIL_MAILBOX_ID}/accounts/{GMAIL_ACCOUNT_ID}/drafts/{draft_id}",
        )
        _clear_local_drafts(GMAIL_ACCOUNT_ID)


# ===================================================================
# Section 6: Auth lifecycle (MUST BE LAST — invalidates session)
# ===================================================================

def test_51_post_auth_logout(e2e_client, flow_state):
    response = e2e_client.post("/auth/logout")
    _assert_ok(response)
    assert response.json() == {"status": "logged_out"}
    flow_state["logged_out"] = "true"


def test_52_get_auth_me_after_logout_401(e2e_client, flow_state):
    _require(flow_state, "logged_out")
    response = e2e_client.get("/auth/me")
    _assert_ok(response, expected=401)
