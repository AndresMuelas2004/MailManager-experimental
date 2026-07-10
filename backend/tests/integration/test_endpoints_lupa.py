"""Tests de integración — operadores de la lupa (from:/to:/subject:/is:/fechas/in:)."""

from __future__ import annotations

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL


# ------------------------------------------------------------------
# Emails — list emails: Gmail-style search operators (lupa)
# ------------------------------------------------------------------
# The seeded migration-0010 rows do NOT carry ``to_email`` / ``has_attachments``
# / favourites / controlled dates, so the operator tests insert a small set of
# controlled rows into a FRESH account (no seed noise) via ``isolated_db`` and
# assert exact ``provider_message_id`` sets + ``total``. This mirrors the direct-
# SQL seeding pattern of test_favorites.py.

def _insert_operator_fixture_rows(isolated_db, account_id):
    """Insert a controlled set of rows for operator assertions.

    Returns nothing; the caller targets the rows by their provider_message_id
    (``op-a`` .. ``op-c``). ``has_attachments`` and ``is_favorite`` are set by
    direct SQL because the sync path (B.lazy) never writes them.
    """
    rows = [
        # id,     from_email,             from_name,  subject,            to_email,       to_name, recv,                       read,  box,        attach, fav
        ("op-a", "recruiter@linkedin.com", "LinkedIn", "Job offer for you", "ana@corp.com", "Ana",  "2026-04-10T09:00:00+00:00", True,  "ALL_MAIL", True,  False),
        ("op-b", "news@github.com",        "GitHub",   "Weekly digest",     "bob@corp.com", "Bob",  "2026-04-20T09:00:00+00:00", False, "ALL_MAIL", False, True),
        ("op-c", "billing@stripe.com",     "Stripe",   "Factura mensual",   "ana@corp.com", "Ana",  "2026-05-01T09:00:00+00:00", True,  "SENT",     False, False),
    ]
    with isolated_db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, to_email, to_name, received_at, is_read,
                 box, has_attachments, is_favorite)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (r[0], account_id, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10])
                for r in rows
            ],
        )


def _ids(body):
    return {row["provider_message_id"] for row in body["items"]}


def test_list_emails_operator_from_matches_email_and_name(
    test_client, setup_mailbox_and_account, isolated_db,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "from:linkedin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == {"op-a"}
    assert body["total"] == 1


def test_list_emails_operator_to_matches_first_recipient(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # ``to:ana`` matches the two rows addressed to ana@corp.com (op-a ALL_MAIL,
    # op-c SENT). op-c is SENT, so with box=ALL_MAIL it is excluded — the to:
    # filter ANDs with the box, leaving only op-a.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "to:ana"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-a"}


def test_list_emails_operator_subject_is_accent_insensitive(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # subject:factura matches "Factura mensual" (op-c, SENT) accent/case-insensitive.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "SENT", "account_id": account_id, "q": "subject:FACTÚRA"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-c"}


def test_list_emails_operator_has_attachment(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Only op-a has has_attachments=TRUE.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "has:attachment"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == {"op-a"}
    assert body["total"] == 1


def test_list_emails_operator_is_unread(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Only op-b is unread in ALL_MAIL.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "is:unread"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-b"}


def test_list_emails_operator_is_read(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Only op-a is read in ALL_MAIL.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "is:read"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-a"}


def test_list_emails_operator_is_favorite_and_starred_alias(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Only op-b is favourite; is:starred is the documented alias of is:favorite.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    for q in ("is:favorite", "is:starred"):
        resp = test_client.get(
            f"{_MAILBOX_URL}/{mailbox_id}/emails",
            params={"box": "ALL_MAIL", "account_id": account_id, "q": q},
        )
        assert resp.status_code == 200, q
        assert _ids(resp.json()) == {"op-b"}, q


def test_list_emails_operator_date_range_is_inclusive_after_exclusive_before(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # op-a 2026-04-10, op-b 2026-04-20 (ALL_MAIL). after:2026/04/10 is inclusive
    # (>=) so op-a is in; before:2026/04/20 is exclusive (<) so op-b is out.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "q": "after:2026/04/10 before:2026/04/20",
        },
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-a"}


def test_list_emails_operator_inverted_date_range_is_empty(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # after a date AND before an earlier date → two incompatible AND clauses →
    # zero rows (the contradiction resolves in SQL, never an error).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "q": "after:2026/05/01 before:2026/01/01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_emails_operator_invalid_date_is_ignored(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # A malformed date drops the filter (no date constraint applied) instead of
    # erroring — so all ALL_MAIL rows come back.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "before:2026/13/40"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-a", "op-b"}


def test_list_emails_operator_in_overrides_box_to_sent(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Route box is ALL_MAIL, but in:sent overrides it: only the SENT row (op-c)
    # comes back and total reflects the overridden box.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "in:sent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == {"op-c"}
    assert body["total"] == 1


def test_list_emails_operator_and_combination_narrows(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # from:linkedin AND has:attachment both hold only for op-a.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "q": "from:linkedin has:attachment",
        },
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-a"}


def test_list_emails_operator_plus_free_text_anded(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Operator + free text: from:github AND the free-text token "digest"
    # (matches op-b subject) → op-b only.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "from:github digest"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-b"}


def test_list_emails_operator_contradiction_is_read_and_unread_is_empty(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # is:read AND is:unread → two incompatible boolean clauses → zero rows.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "is:read is:unread"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_emails_operator_quoted_phrase_matches_contiguous_only(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # subject:"Job offer" matches op-a (contiguous phrase). The reversed two-word
    # phrase "offer Job" is not a contiguous substring of any subject → empty.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    hit = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": 'subject:"Job offer"'},
    )
    assert hit.status_code == 200
    assert _ids(hit.json()) == {"op-a"}
    miss = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": 'subject:"offer Job"'},
    )
    assert miss.status_code == 200
    assert miss.json()["items"] == []


def test_list_emails_unknown_operator_is_searched_literally(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # foo:bar is not a known operator → treated as a literal free-text token.
    # No row contains "foo:bar", so the result is empty (NOT a 422). A 200 with
    # an empty page proves the tolerance policy.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "q": "foo:bar"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_list_emails_operator_query_never_422_on_content(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # A query made only of operators (well above the 2-char floor, under 200)
    # is accepted — the router only rejects on length, never on operator content.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "q": "is:importante has:drive from:",
        },
    )
    assert resp.status_code == 200
