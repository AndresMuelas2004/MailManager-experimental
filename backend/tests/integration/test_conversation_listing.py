"""
Integration tests for the conversation-grouping listing.

``GET /mailboxes/{mid}/emails?group_by_thread=true`` collapses each thread
into one representative row (its most-recent message) with aggregated
``is_read`` / ``has_attachments`` / ``is_favorite`` and a per-box
``thread_message_count``; ``total`` counts threads. These run against the
real database (the listing reads only from the local copy — no provider
call), seeding ``email_metadata`` rows directly so the grouping semantics
and the (account_id, thread_key) vs thread_key key asymmetry are pinned
end-to-end (window functions cannot run with a fake cursor).
"""

from __future__ import annotations

import psycopg2.extras

from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_GMAIL_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
    SEEDED_OUTLOOK_ACCOUNT_ID as _SEEDED_OUTLOOK_ACCOUNT,
    TEST_USER_ID,
)

_VMB_URL = "/virtual-mailboxes"


def _insert_email(
    isolated_db,
    *,
    account_id: str,
    provider_message_id: str,
    thread_id: str | None,
    received_at: str,
    box: str = "ALL_MAIL",
    is_read: bool = True,
    has_attachments: bool = False,
    is_favorite: bool = False,
    subject: str = "s",
    to_email: str = "me@x.com",
    to_name: str = "Me",
) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box,
                 has_attachments, is_favorite, to_email, to_name)
            VALUES (%(pmid)s, %(aid)s, %(thread)s, 'sender@x.com', 'Sender',
                    %(subject)s, %(received)s, %(is_read)s, %(box)s,
                    %(has_att)s, %(is_fav)s, %(to_email)s, %(to_name)s)
            """,
            {
                "pmid": provider_message_id,
                "aid": account_id,
                "thread": thread_id,
                "subject": subject,
                "received": received_at,
                "is_read": is_read,
                "box": box,
                "has_att": has_attachments,
                "is_fav": is_favorite,
                "to_email": to_email,
                "to_name": to_name,
            },
        )


def _reparent_seeded_mailbox(isolated_db, mailbox_id: str, owner_user_id: str) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE mailboxes SET owner_user_id = %s WHERE mailbox_id = %s",
            (owner_user_id, mailbox_id),
        )


def _list_grouped(client, mailbox_id: str, account_id: str | None = None, **params):
    query = {"box": "ALL_MAIL", "group_by_thread": "true", **params}
    if account_id is not None:
        query["account_id"] = account_id
    return client.get(f"{_MAILBOX_URL}/{mailbox_id}/emails", params=query)


# ---------------------------------------------------------------------------
# Regular grouped listing
# ---------------------------------------------------------------------------


def test_grouped_listing_collapses_thread_to_one_representative_row(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # One thread of 3 messages + a standalone single-message thread.
    _insert_email(isolated_db, account_id=account_id, provider_message_id="t1-a",
                  thread_id="thr-1", received_at="2026-05-01T09:00:00+00:00", subject="oldest")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="t1-b",
                  thread_id="thr-1", received_at="2026-05-01T10:00:00+00:00", subject="middle")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="t1-c",
                  thread_id="thr-1", received_at="2026-05-01T11:00:00+00:00", subject="newest")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="t2-a",
                  thread_id="thr-2", received_at="2026-05-02T09:00:00+00:00", subject="solo")

    body = _list_grouped(test_client, mailbox_id, account_id).json()
    rows = body["items"]
    # Two threads → two rows; total counts threads, not messages.
    assert body["total"] == 2
    assert len(rows) == 2
    by_thread = {r["thread_id"]: r for r in rows}
    # The representative of thr-1 is its most-recent message (11:00).
    assert by_thread["thr-1"]["provider_message_id"] == "t1-c"
    assert by_thread["thr-1"]["thread_message_count"] == 3
    # The single-message thread carries count 1.
    assert by_thread["thr-2"]["provider_message_id"] == "t2-a"
    assert by_thread["thr-2"]["thread_message_count"] == 1
    # Outer ordering is most-recent thread first.
    assert rows[0]["thread_id"] == "thr-2"


def test_grouped_listing_aggregates_read_attachments_favorite(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Thread of 3: mixed is_read, one with an attachment, one favourite.
    _insert_email(isolated_db, account_id=account_id, provider_message_id="a1",
                  thread_id="agg", received_at="2026-05-01T09:00:00+00:00",
                  is_read=True, has_attachments=False, is_favorite=False)
    _insert_email(isolated_db, account_id=account_id, provider_message_id="a2",
                  thread_id="agg", received_at="2026-05-01T10:00:00+00:00",
                  is_read=False, has_attachments=True, is_favorite=False)
    _insert_email(isolated_db, account_id=account_id, provider_message_id="a3",
                  thread_id="agg", received_at="2026-05-01T11:00:00+00:00",
                  is_read=True, has_attachments=False, is_favorite=True)

    row = _list_grouped(test_client, mailbox_id, account_id).json()["items"][0]
    # bool_and(is_read): one unread message → the thread reads as unread.
    assert row["is_read"] is False
    # bool_or(has_attachments): at least one attachment → the clip shows.
    assert row["has_attachments"] is True
    # bool_or(is_favorite): at least one favourite → the star shows.
    assert row["is_favorite"] is True
    assert row["thread_message_count"] == 3


def test_grouped_listing_chip_surfaces_thread_when_any_message_matches(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Quick-filter chips apply at the MESSAGE level, then grouping collapses.
    # A thread surfaces if ANY of its messages matches the chip, and its
    # representative is the most-recent message overall (not the most-recent
    # matching one — the aggregation keeps the thread's newest row as the
    # representative). Here only the middle message is unread; the unread chip
    # still surfaces the thread, represented by its newest message.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="u1",
                  thread_id="uthr", received_at="2026-05-01T09:00:00+00:00", is_read=True)
    _insert_email(isolated_db, account_id=account_id, provider_message_id="u2",
                  thread_id="uthr", received_at="2026-05-01T10:00:00+00:00", is_read=False)
    _insert_email(isolated_db, account_id=account_id, provider_message_id="u3",
                  thread_id="uthr", received_at="2026-05-01T11:00:00+00:00", is_read=True)
    # A fully-read thread that must NOT surface under the unread chip.
    _insert_email(isolated_db, account_id=account_id, provider_message_id="r1",
                  thread_id="rthr", received_at="2026-05-02T09:00:00+00:00", is_read=True)

    body = _list_grouped(test_client, mailbox_id, account_id, unread="true").json()
    rows = body["items"]
    assert body["total"] == 1
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "uthr"
    # Representative is the thread's most-recent message (11:00), even though
    # the matching message was the 10:00 one.
    assert rows[0]["provider_message_id"] == "u3"
    # The whole thread is present in the box, so the count is the full 3.
    assert rows[0]["thread_message_count"] == 3


def test_grouped_listing_sort_subject_orders_thread_representatives(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # sort applies to the collapsed thread rows (their representatives). Two
    # single-message threads ordered by subject asc: "alpha" < "omega".
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="z-rep",
                  thread_id="z", received_at="2026-05-02T09:00:00+00:00", subject="omega")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="a-rep",
                  thread_id="a", received_at="2026-05-01T09:00:00+00:00", subject="alpha")

    body = _list_grouped(
        test_client, mailbox_id, account_id, sort="subject", sort_dir="asc",
    ).json()
    # Without the sort the default (date desc) would put z-rep first; subject
    # asc flips it to a-rep ("alpha") then z-rep ("omega").
    assert [r["provider_message_id"] for r in body["items"]] == ["a-rep", "z-rep"]
    assert body["total"] == 2


def test_grouped_listing_threadless_messages_stay_individual(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # Two messages with empty / NULL thread_id must NEVER merge — each is a
    # singleton keyed by its own provider_message_id.
    _insert_email(isolated_db, account_id=account_id, provider_message_id="loose-1",
                  thread_id="", received_at="2026-05-01T09:00:00+00:00")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="loose-2",
                  thread_id=None, received_at="2026-05-01T10:00:00+00:00")

    body = _list_grouped(test_client, mailbox_id, account_id).json()
    rows = body["items"]
    assert body["total"] == 2
    ids = {r["provider_message_id"] for r in rows}
    assert ids == {"loose-1", "loose-2"}
    assert all(r["thread_message_count"] == 1 for r in rows)


def test_ungrouped_listing_returns_one_row_per_message(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """Favourites and the classic inbox omit group_by_thread → no collapsing."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="m1",
                  thread_id="shared", received_at="2026-05-01T09:00:00+00:00")
    _insert_email(isolated_db, account_id=account_id, provider_message_id="m2",
                  thread_id="shared", received_at="2026-05-01T10:00:00+00:00")

    body = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id},
    ).json()
    # Classic behaviour: both messages of the thread appear as separate rows.
    assert body["total"] == 2
    assert {r["provider_message_id"] for r in body["items"]} == {"m1", "m2"}
    # thread_message_count defaults to 1 for every ungrouped row.
    assert all(r["thread_message_count"] == 1 for r in body["items"])


def test_grouped_listing_does_not_merge_distinct_accounts_sharing_thread_id(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """Regular grouping partitions by (account_id, thread_key): two DISTINCT
    accounts under the same unified mailbox that (pathologically) share the
    same thread_id string must surface as TWO rows — accounts never merge."""
    mailbox_id, account_a = setup_mailbox_and_account(test_client, "gmail")
    # A second account under the SAME mailbox.
    acc_b = test_client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts",
        json={"provider": "outlook", "display_label": "second"},
    )
    account_b = acc_b.json()["account_id"]

    _insert_email(isolated_db, account_id=account_a, provider_message_id="acc-a-msg",
                  thread_id="same-string", received_at="2026-05-01T09:00:00+00:00")
    _insert_email(isolated_db, account_id=account_b, provider_message_id="acc-b-msg",
                  thread_id="same-string", received_at="2026-05-01T10:00:00+00:00")

    # Unified listing (no account_id) over the whole mailbox.
    body = _list_grouped(test_client, mailbox_id).json()
    rows = body["items"]
    # Same thread_id string but two distinct accounts → two rows, not one.
    assert body["total"] == 2
    account_ids = {r["account_id"] for r in rows}
    assert account_ids == {account_a, account_b}
    assert all(r["thread_message_count"] == 1 for r in rows)


def test_grouped_listing_paginates_without_splitting_threads(
    test_client, setup_mailbox_and_account, isolated_db,
):
    """A page is built from collapsed thread rows, so a thread is never split
    across pages and ``total`` is the thread count regardless of page size."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    # 3 threads, each with 2 messages (6 rows total in the table).
    for t in range(3):
        for m in range(2):
            _insert_email(
                isolated_db, account_id=account_id,
                provider_message_id=f"t{t}-m{m}", thread_id=f"thread-{t}",
                received_at=f"2026-05-0{t + 1}T0{m + 9}:00:00+00:00",
            )

    page1 = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id,
                "group_by_thread": "true", "limit": 2, "offset": 0},
    ).json()
    page2 = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id,
                "group_by_thread": "true", "limit": 2, "offset": 2},
    ).json()
    # total counts threads (3), never the 6 underlying messages.
    assert page1["total"] == 3
    assert page2["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    # No thread appears on both pages.
    threads_p1 = {r["thread_id"] for r in page1["items"]}
    threads_p2 = {r["thread_id"] for r in page2["items"]}
    assert threads_p1.isdisjoint(threads_p2)
    # Every thread row carries its full 2-message count even when paginated.
    all_rows = page1["items"] + page2["items"]
    assert all(r["thread_message_count"] == 2 for r in all_rows)


# ---------------------------------------------------------------------------
# Virtual mailbox grouped listing (always groups + dedups cross-account)
# ---------------------------------------------------------------------------


def test_virtual_grouped_listing_dedups_same_provider_account_under_two_mailboxes(
    test_client, isolated_db,
):
    """A vmbox spanning the SAME provider account connected under two real
    mailboxes (same provider_message_id + thread_id) must collapse to ONE
    thread row, and ``total`` (COUNT(DISTINCT thread_key)) must not
    over-count. Genuinely distinct messages of the thread still aggregate."""
    _reparent_seeded_mailbox(isolated_db, _SEEDED_GMAIL_MAILBOX, TEST_USER_ID)
    from tests.integration.conftest import SEEDED_OUTLOOK_MAILBOX_ID
    _reparent_seeded_mailbox(isolated_db, SEEDED_OUTLOOK_MAILBOX_ID, TEST_USER_ID)

    # The same message (provider_message_id + thread) surfaced under two
    # accounts (the cross-mailbox duplicate the vmbox must collapse) ...
    _insert_email(isolated_db, account_id=_SEEDED_GMAIL_ACCOUNT,
                  provider_message_id="conv-dup", thread_id="vthread",
                  received_at="2026-05-10T10:00:00+00:00", subject="conv group",
                  to_email="real@x.com")
    _insert_email(isolated_db, account_id=_SEEDED_OUTLOOK_ACCOUNT,
                  provider_message_id="conv-dup", thread_id="vthread",
                  received_at="2026-05-10T09:00:00+00:00", subject="conv group",
                  to_email="")
    # ... plus a genuinely distinct earlier message of the same thread.
    _insert_email(isolated_db, account_id=_SEEDED_GMAIL_ACCOUNT,
                  provider_message_id="conv-first", thread_id="vthread",
                  received_at="2026-05-09T08:00:00+00:00", subject="conv group")

    create = test_client.post(_VMB_URL, json={
        "display_name": "Conv vmbox",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {"subject_contains": "conv group"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    body = test_client.get(f"{_VMB_URL}/{vmb_id}/emails").json()
    rows = body["items"]
    # One thread row despite three table rows across two accounts.
    assert len(rows) == 1
    assert body["total"] == 1
    row = rows[0]
    assert row["thread_id"] == "vthread"
    # Representative is the most-recent message after cross-account dedup.
    assert row["provider_message_id"] == "conv-dup"
    # thread_message_count counts the deduplicated members (2: conv-dup +
    # conv-first), NOT the 3 raw rows.
    assert row["thread_message_count"] == 2
