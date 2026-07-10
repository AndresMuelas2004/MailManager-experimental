"""Tests de integración — listado de correos: vistas, paginación y búsqueda básica."""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_GMAIL_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
    SEEDED_OUTLOOK_ACCOUNT_ID as _SEEDED_OUTLOOK_ACCOUNT,
    SEEDED_OUTLOOK_MAILBOX_ID as _SEEDED_OUTLOOK_MAILBOX,
)


# ------------------------------------------------------------------
# Emails — list emails
# ------------------------------------------------------------------

def test_list_emails_unified_view(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails?box=ALL_MAIL"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Paginated envelope: exact keys + total of the WHOLE filtered set.
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert body["total"] == 30
    data = body["items"]
    assert len(data) == 30
    item = data[0]
    assert "provider_message_id" in item
    assert "account_id" in item
    assert "from_email" in item
    assert "subject" in item
    assert "received_at" in item
    assert "is_read" in item
    assert all(e["box"] == "ALL_MAIL" for e in data)
    assert all(e["account_id"] == _SEEDED_GMAIL_ACCOUNT for e in data)


def test_list_emails_single_account_view(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_OUTLOOK_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_OUTLOOK_ACCOUNT},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) == 30
    assert all(e["account_id"] == _SEEDED_OUTLOOK_ACCOUNT for e in data)
    assert all(e["box"] == "ALL_MAIL" for e in data)


def test_list_emails_invalid_box_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails?box=INVALID"
    )
    assert resp.status_code == 422


def test_list_emails_missing_box_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails"
    )
    assert resp.status_code == 422


def test_list_emails_nonexistent_account_returns_404(seeded_test_client):
    fake_account_id = "00000000-0000-4000-a000-000000000099"
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": fake_account_id},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_list_emails_nonexistent_mailbox_returns_404(seeded_test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    resp = seeded_test_client.get(f"{_MAILBOX_URL}/{fake_mailbox_id}/emails?box=ALL_MAIL")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


# ------------------------------------------------------------------
# Emails — unread count badge (GET /emails/unread-count)
# ------------------------------------------------------------------
# Exact expected counts come from the migration-0010 Gmail seed (single
# account in that mailbox): unread (is_read=FALSE) rows are 12 in ALL_MAIL
# and 6 in SPAM — counting INDIVIDUAL messages (the endpoint never groups
# by thread).

def test_unread_count_default_box_is_all_mail(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails/unread-count"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"mailbox_id", "box", "total", "accounts"}
    assert body["mailbox_id"] == _SEEDED_GMAIL_MAILBOX
    assert body["box"] == "ALL_MAIL"
    assert body["total"] == 12
    # The Gmail seed mailbox has exactly one account; its breakdown carries
    # the whole total.
    assert body["accounts"] == [{"account_id": _SEEDED_GMAIL_ACCOUNT, "unread": 12}]


def test_unread_count_spam_box(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails/unread-count",
        params={"box": "SPAM"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["box"] == "SPAM"
    assert body["total"] == 6
    assert body["accounts"] == [{"account_id": _SEEDED_GMAIL_ACCOUNT, "unread": 6}]


@pytest.mark.parametrize("box", ["TRASH", "SENT"])
def test_unread_count_box_outside_allmail_spam_returns_422(seeded_test_client, box):
    # The router Literal only accepts ALL_MAIL | SPAM; SENT / TRASH have no
    # unread badge in the UI, so they collapse to a 422 (FastAPI validation).
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails/unread-count",
        params={"box": box},
    )
    assert resp.status_code == 422


def test_unread_count_nonexistent_mailbox_returns_404(seeded_test_client):
    fake_mailbox_id = "00000000-0000-4000-a000-000000000099"
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{fake_mailbox_id}/emails/unread-count?box=ALL_MAIL"
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mailbox_not_found"


def _insert_unread_rows(isolated_db, account_id, *, box, unread, read=0):
    """Insert ``unread`` unread + ``read`` read email_metadata rows in ``box``.

    Gives one account a controlled unread count for the badge tests. Only
    ``is_read`` / ``box`` matter to the counter; the rest are filler.
    """
    rows = [(f"u-{account_id[:8]}-{box}-{i}", False) for i in range(unread)]
    rows += [(f"r-{account_id[:8]}-{box}-{i}", True) for i in range(read)]
    with isolated_db.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, to_email, to_name, received_at, is_read, box)
            VALUES (%s, %s, NULL, 'x@e.com', 'X', 'S', '', '', now(), %s, %s)
            """,
            [(pmid, account_id, is_read, box) for (pmid, is_read) in rows],
        )


def test_unread_count_sums_across_multiple_accounts(test_client, setup_mailbox_and_account, isolated_db):
    # Two accounts in one mailbox with DISTINCT unread counts. ``total`` is
    # summed in Python from the per-account breakdown, so 2 + 3 must give 5 —
    # a single-account seed cannot distinguish "summed" from "echoed".
    mid, acc1 = setup_mailbox_and_account(test_client, "gmail")
    acc2 = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "acc2"},
    ).json()["account_id"]
    _insert_unread_rows(isolated_db, acc1, box="ALL_MAIL", unread=2)
    _insert_unread_rows(isolated_db, acc2, box="ALL_MAIL", unread=3)
    resp = test_client.get(f"{_MAILBOX_URL}/{mid}/emails/unread-count?box=ALL_MAIL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert {a["account_id"]: a["unread"] for a in body["accounts"]} == {acc1: 2, acc2: 3}


def test_unread_count_account_without_unread_is_zero_filled(test_client, setup_mailbox_and_account, isolated_db):
    # The COUNT_UNREAD_BY_ACCOUNT GROUP BY emits no row for an account with
    # zero unread; the service must still surface it as unread:0. acc2 has no
    # rows at all; acc1's read row must NOT count (only is_read=FALSE counts).
    mid, acc1 = setup_mailbox_and_account(test_client, "gmail")
    acc2 = test_client.post(
        f"{_MAILBOX_URL}/{mid}/accounts",
        json={"provider": "gmail", "display_label": "acc2"},
    ).json()["account_id"]
    _insert_unread_rows(isolated_db, acc1, box="ALL_MAIL", unread=2, read=1)
    resp = test_client.get(f"{_MAILBOX_URL}/{mid}/emails/unread-count?box=ALL_MAIL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {a["account_id"]: a["unread"] for a in body["accounts"]} == {acc1: 2, acc2: 0}


# ------------------------------------------------------------------
# Seeded GET endpoint tests — exact count per box (migration 0010)
# ------------------------------------------------------------------

@pytest.mark.parametrize("box, expected_count", [
    ("ALL_MAIL", 30),
    ("SENT", 10),
    ("TRASH", 4),
    ("SPAM", 6),
])
def test_seeded_list_emails_by_account(seeded_test_client, box, expected_count):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": box, "account_id": _SEEDED_GMAIL_ACCOUNT},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == expected_count
    data = body["items"]
    assert len(data) == expected_count
    assert all(e["account_id"] == _SEEDED_GMAIL_ACCOUNT for e in data)
    assert all(e["box"] == box for e in data)


@pytest.mark.parametrize("box, expected_count", [
    ("ALL_MAIL", 30),
    ("SENT", 10),
    ("TRASH", 4),
    ("SPAM", 6),
])
def test_seeded_list_emails_by_mailbox(seeded_test_client, box, expected_count):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": box},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == expected_count
    data = body["items"]
    assert len(data) == expected_count
    assert all(e["box"] == box for e in data)


# ------------------------------------------------------------------
# Emails — list emails: search, limit, offset (lupa MVP)
# ------------------------------------------------------------------

def test_list_emails_q_too_short_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "q": "a"},
    )
    assert resp.status_code == 422


def test_list_emails_q_empty_string_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "q": ""},
    )
    assert resp.status_code == 422


def test_list_emails_q_two_chars_is_accepted(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "q": "ab"},
    )
    assert resp.status_code == 200


def test_list_emails_q_too_long_returns_422(seeded_test_client):
    # Router enforces max_length=200.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "q": "x" * 201},
    )
    assert resp.status_code == 422


def test_list_emails_limit_zero_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "limit": 0},
    )
    assert resp.status_code == 422


def test_list_emails_limit_above_max_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "limit": 600},
    )
    assert resp.status_code == 422


def test_list_emails_limit_at_max_is_accepted(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "limit": 500},
    )
    assert resp.status_code == 200


def test_list_emails_offset_negative_returns_422(seeded_test_client):
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "offset": -1},
    )
    assert resp.status_code == 422


def test_list_emails_search_case_insensitive(seeded_test_client):
    # Seeded subject "Sprint planning - semana 12" lives only on Gmail account.
    # Different case of the same word must still match.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "SPRINT"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) >= 1
    # Every returned row must contain "sprint" somewhere in the searchable
    # columns (subject / from_email / from_name) — case-insensitive.
    for e in data:
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert "sprint" in haystack


def test_list_emails_search_accent_insensitive_via_unaccent(seeded_test_client):
    # Seeded sender "Karen López" — search without the accent must still match.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "lopez"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) >= 1
    matched = [
        e for e in data
        if (e.get("from_name") or "").lower().replace("ó", "o") == "karen lopez"
    ]
    assert matched, "search 'lopez' should accent-insensitively match seeded 'Karen López'"


def test_list_emails_search_typo_does_not_match(seeded_test_client):
    # "facutra" is a typo; Opción A does not tolerate typos. There is a seeded
    # subject "Factura #4521 adjunta" — the search must NOT return it.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "facutra"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Whatever matches "facutra" as a substring is fine; what must NOT happen
    # is matching "Factura" through fuzzy/typo tolerance.
    for e in data:
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert "factura" not in haystack


def test_list_emails_search_percent_is_literal(seeded_test_client):
    # _escape_like must neutralise % so the ILIKE pattern matches the literal
    # two-char substring "%a" — not the SQL wildcard. Router rejects q="%"
    # (min_length=2), so the probe is "%a". No seeded row contains that exact
    # substring, so a regression that dropped the escape (turning % into the
    # ILIKE wildcard) would return rows instead of an empty list.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "%a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_emails_search_underscore_is_literal(seeded_test_client):
    # Same shape as the % test, for the underscore wildcard. _escape_like must
    # turn it into a literal so q="_a" matches a literal "_a" substring (which
    # no seeded row contains), not the "any single char + a" SQL wildcard.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "_a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_emails_search_matches_in_from_email_only(seeded_test_client):
    # Seeded row gmail-allmail-012 has from_email="karen@hr.com"; the substring
    # "hr.com" appears in no other row's subject, from_name, or from_email.
    # This pins the from_email branch of the OR predicate end-to-end against
    # real PostgreSQL — a regression that dropped from_email from the search
    # would return zero rows instead of the Karen López row.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "hr.com"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) >= 1
    assert all("hr.com" in (row.get("from_email") or "").lower() for row in data)
    # Load-bearing assertion: at least one returned row matches ONLY in from_email.
    assert any(
        "hr.com" in (row.get("from_email") or "").lower()
        and "hr.com" not in (row.get("subject") or "").lower()
        and "hr.com" not in (row.get("from_name") or "").lower()
        for row in data
    )


def test_list_emails_search_multi_word_is_AND(seeded_test_client):
    # "Sprint planning - semana 12" matches both "sprint" and "planning".
    # "Sprint planning - tareas asignadas" also matches both.
    # No other seeded row contains both words.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": _SEEDED_GMAIL_ACCOUNT,
            "q": "Sprint planning",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) >= 1
    # Every returned row must contain BOTH tokens in the union of searchable columns.
    for e in data:
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert "sprint" in haystack
        assert "planning" in haystack


def test_list_emails_search_with_unified_view_matches_only_in_mailbox(seeded_test_client):
    # The Gmail mailbox holds only the Gmail seeded account; rows from the
    # Outlook mailbox must not bleed into the unified view of the Gmail mailbox.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "q": "factura"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Whatever rows are returned, none of them belong to the Outlook account.
    assert all(e["account_id"] != _SEEDED_OUTLOOK_ACCOUNT for e in data)
    # And every row must actually contain the search token.
    for e in data:
        haystack = " ".join([
            (e.get("subject") or ""),
            (e.get("from_email") or ""),
            (e.get("from_name") or ""),
        ]).lower()
        assert "factura" in haystack


@pytest.mark.parametrize(
    ("box", "q"),
    [
        ("SENT", "solicitud"),  # appears only in SENT subjects
        ("TRASH", "semanal"),   # appears only in TRASH subjects
        ("SPAM", "urgent"),     # appears only in SPAM subjects
    ],
)
def test_list_emails_search_respects_box(seeded_test_client, box, q):
    # docs/features/lupa.md contractualises "la lupa filtra dentro del box".
    # Each (box, q) probe targets a token unique to that box on the Gmail seed,
    # so a regression dropping the box filter when q is supplied would surface
    # as rows from other boxes leaking into the response.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": box, "account_id": _SEEDED_GMAIL_ACCOUNT, "q": q},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert len(data) >= 1
    assert all(e["box"] == box for e in data)
    needle = q.lower()
    for row in data:
        haystack = " ".join([
            (row.get("subject") or ""),
            (row.get("from_email") or ""),
            (row.get("from_name") or ""),
        ]).lower()
        assert needle in haystack


def test_list_emails_search_matches_in_subject_only(seeded_test_client):
    # Seeded subjects "Actualización del presupuesto Q1" (gmail-allmail-004) and
    # its reply (gmail-allmail-005) contain the substring "presupuesto"; neither
    # the senders ("dave@corp.com" / "Dave Wilson", "eve@corp.com" / "Eve
    # Thompson") nor any other Gmail ALL_MAIL row contain it. Pins the subject
    # branch of the OR predicate end-to-end against real PostgreSQL — a
    # regression that dropped subject from the search would return zero rows.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "presupuesto"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 2 seeded rows in Gmail ALL_MAIL contain "presupuesto":
    # gmail-allmail-004 and gmail-allmail-005 (the reply on the same thread).
    # gmail-sent-002 also contains it but lives in box=SENT, so it is excluded.
    assert {row["provider_message_id"] for row in data} == {
        "gmail-allmail-004", "gmail-allmail-005",
    }
    assert all("presupuesto" in (row.get("subject") or "").lower() for row in data)
    # Load-bearing assertion: every returned row matches ONLY in subject —
    # neither from_email ("dave@corp.com" / "eve@corp.com") nor from_name
    # ("Dave Wilson" / "Eve Thompson") contains the token.
    assert all(
        "presupuesto" not in (row.get("from_email") or "").lower()
        and "presupuesto" not in (row.get("from_name") or "").lower()
        for row in data
    )


def test_list_emails_search_matches_in_from_name_only(seeded_test_client):
    # Seeded sender "Irene Salazar" (gmail-allmail-009) — the substring "salazar"
    # appears in no other row's subject, from_email, or from_name across the
    # Gmail seed (subject is "Contrato pendiente de firma", email is
    # "irene@legal.com"). Pins the from_name branch of the OR predicate; a
    # regression that dropped from_name from the search would return zero rows.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "salazar"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 1 seeded row contains "salazar" anywhere — gmail-allmail-009.
    assert [row["provider_message_id"] for row in data] == ["gmail-allmail-009"]
    row = data[0]
    assert "salazar" in (row.get("from_name") or "").lower()
    # Load-bearing assertion: the match is ONLY in from_name.
    assert "salazar" not in (row.get("subject") or "").lower()
    assert "salazar" not in (row.get("from_email") or "").lower()


def test_list_emails_search_accent_at_word_start(seeded_test_client):
    # Seeded subjects "Análisis competitivo Q1 - presentación" (outlook-allmail-023)
    # and "Re: Análisis competitivo - datos extra" (outlook-allmail-024) start
    # with the accented character "Á". Searching "analisis" without the accent
    # must match through unaccent — covering the case where the diacritic is on
    # the FIRST character of the word, complementing the existing "lopez" →
    # "López" test which targets accent in the middle of a word.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_OUTLOOK_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_OUTLOOK_ACCOUNT, "q": "analisis"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 2 seeded Outlook ALL_MAIL rows contain "Análisis":
    # outlook-allmail-023 and outlook-allmail-024 (the reply on the same thread).
    assert {row["provider_message_id"] for row in data} == {
        "outlook-allmail-023", "outlook-allmail-024",
    }
    assert all(
        "análisis" in (row.get("subject") or "").lower()
        for row in data
    )


def test_list_emails_search_accent_in_subject_middle(seeded_test_client):
    # Seeded subject "Recordatorio: evaluación de desempeño" (gmail-allmail-012)
    # has the accent IN THE MIDDLE of "evaluación" (over the second "o"). The
    # existing "lopez" → "López" test pins accent-in-middle behaviour for the
    # from_name column; this test pins the same behaviour for the subject column.
    # The token "evaluacion" appears in no other row's subject / from_email /
    # from_name, so all matches come from this single row's subject.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "evaluacion"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 1 seeded row contains "evaluación" — gmail-allmail-012.
    assert [row["provider_message_id"] for row in data] == ["gmail-allmail-012"]
    assert "evaluación" in (data[0].get("subject") or "").lower()


def test_list_emails_search_mixed_case_with_accent(seeded_test_client):
    # Seeded sender "Karen López" (gmail-allmail-012). Combining all-uppercase
    # with an accented character in the same query (q="LÓPEZ") must still match
    # the mixed-case stored value. Pins the interaction between case folding
    # (lower()) and accent stripping (unaccent()) inside the SQL predicate.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "LÓPEZ"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 1 seeded row contains "López" — gmail-allmail-012 ("Karen López").
    assert [row["provider_message_id"] for row in data] == ["gmail-allmail-012"]
    assert "lópez" in (data[0].get("from_name") or "").lower()


def test_list_emails_search_unaccent_handles_n_with_tilde(seeded_test_client):
    # Seeded subjects "Campaña de lanzamiento - borrador" (gmail-allmail-014)
    # and "Re: Campaña de lanzamiento - aprobado" (gmail-allmail-015) contain
    # the Spanish "ñ". PostgreSQL's standard unaccent rules transform "ñ" → "n",
    # so q="campana" (without the tilde) must match "Campaña". This covers a
    # class of accent different from the typical á/é/í/ó/ú vowel diacritics
    # already exercised by the "lopez" / "evaluacion" tests.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "campana"},
    )
    assert resp.status_code == 200
    data = resp.json()["items"]
    # Exactly 2 seeded rows contain "Campaña":
    # gmail-allmail-014 and gmail-allmail-015 (the reply on the same thread).
    assert {row["provider_message_id"] for row in data} == {
        "gmail-allmail-014", "gmail-allmail-015",
    }
    assert all(
        "campaña" in (row.get("subject") or "").lower()
        for row in data
    )


def test_list_emails_limit_caps_returned_rows(seeded_test_client):
    # ALL_MAIL has 30 seeded rows for Gmail. limit=5 must trim the PAGE, but
    # total must still report the WHOLE filtered set (30), not the page size.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5
    assert body["total"] == 30
    assert body["limit"] == 5
    assert body["offset"] == 0


def test_list_emails_offset_skips_initial_rows(seeded_test_client):
    # With limit 5 and offset 5, the second page must differ from the first,
    # and total must stay constant (30) across pages — proving the count is
    # over the whole filtered set, not the page.
    first_json = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "limit": 5, "offset": 0},
    ).json()
    second_json = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "limit": 5, "offset": 5},
    ).json()
    first = first_json["items"]
    second = second_json["items"]
    assert len(first) == 5
    assert len(second) == 5
    assert first_json["total"] == second_json["total"] == 30
    first_ids = {e["provider_message_id"] for e in first}
    second_ids = {e["provider_message_id"] for e in second}
    assert first_ids.isdisjoint(second_ids)


def test_list_emails_offset_beyond_total_returns_empty_page_with_total(seeded_test_client):
    # offset past the end of the filtered set: empty page, correct total, 200.
    # (The frontend reubicates the user to the last valid page on this.)
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "limit": 5, "offset": 1000},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 30
    assert body["offset"] == 1000


def test_list_emails_search_affects_total(seeded_test_client):
    # A q that matches a known number of rows must drive total to that number,
    # not to the box's full size — total counts the FILTERED set.
    # "presupuesto" matches exactly 2 Gmail ALL_MAIL rows
    # (gmail-allmail-004 / -005), same as test_list_emails_search_matches_in_subject_only.
    resp = seeded_test_client.get(
        f"{_MAILBOX_URL}/{_SEEDED_GMAIL_MAILBOX}/emails",
        params={"box": "ALL_MAIL", "account_id": _SEEDED_GMAIL_ACCOUNT, "q": "presupuesto"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
