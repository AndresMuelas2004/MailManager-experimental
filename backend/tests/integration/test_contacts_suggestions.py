"""
Integration tests for the recipient-autocomplete endpoint
``GET /contacts/suggestions`` (real DB, per-test rollback).

The endpoint is user-level and reads only from the local DB (no provider
call), so it uses ``test_client_base`` directly — the provider-fake
``test_client`` fixture is unnecessary (``contacts_service`` never builds
a manager). Auth is the session-wide override to ``TEST_USER_ID``.

Why custom-inserted rows instead of ``seeded_test_client`` (migration
0010): the seed predates migration 0031, so every seeded row has empty
``to_email`` / ``to_name`` and its ``from_email`` is the account's own
address (which the own-address exclusion drops). The seed therefore
cannot exercise sent-recipient suggestions, own-address exclusion via a
``to_email``, or the duplicate-name tie-break — so this feature inserts
bespoke ``email_metadata`` rows for ``TEST_USER_ID`` via
``isolated_db.cursor()`` (an accepted, already-used integration pattern).
"""

from __future__ import annotations

from api.services import contacts_service
from database import QueryError


CONTACTS_URL = "/contacts/suggestions"

_OTHER_USER_ID = "00000000-0000-4000-a000-0000000000ff"
_OTHER_MAILBOX_ID = "00000000-0000-4000-a000-0000000000fe"
_OTHER_ACCOUNT_ID = "00000000-0000-4000-a000-0000000000fd"


def _insert_received(cur, account_id, *, pmid, from_email, from_name, box="ALL_MAIL",
                     received_at="2026-05-10T10:00:00+00:00"):
    """A received row: candidate comes from ``from_email`` / ``from_name``."""
    cur.execute(
        """
        INSERT INTO email_metadata
            (provider_message_id, account_id, thread_id, from_email,
             from_name, subject, received_at, is_read, box, to_email, to_name)
        VALUES (%s, %s, NULL, %s, %s, 'subject', %s, FALSE, %s, '', '')
        """,
        (pmid, account_id, from_email, from_name, received_at, box),
    )


def _insert_sent(cur, account_id, *, pmid, to_email, to_name,
                 received_at="2026-05-10T10:00:00+00:00"):
    """A sent row: candidate comes from ``to_email`` / ``to_name``."""
    cur.execute(
        """
        INSERT INTO email_metadata
            (provider_message_id, account_id, thread_id, from_email,
             from_name, subject, received_at, is_read, box, to_email, to_name)
        VALUES (%s, %s, NULL, 'me@account.test', 'Me', 'subject', %s,
                FALSE, 'SENT', %s, %s)
        """,
        (pmid, account_id, received_at, to_email, to_name),
    )


def _set_account_email(cur, account_id, email_address):
    cur.execute(
        "UPDATE accounts SET email_address = %s WHERE account_id = %s",
        (email_address, account_id),
    )


# ---------------------------------------------------------------------------
# Aggregation behaviour
# ---------------------------------------------------------------------------


def test_received_sender_is_suggested(test_client_base, setup_mailbox_and_account, isolated_db):
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="r1",
                         from_email="alice@example.com", from_name="Alice")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "ali"})
    assert resp.status_code == 200, resp.text
    emails = {item["email"] for item in resp.json()}
    assert "alice@example.com" in emails
    # The matching item carries the display name.
    item = next(i for i in resp.json() if i["email"] == "alice@example.com")
    assert item["name"] == "Alice"


def test_sent_recipient_is_suggested(test_client_base, setup_mailbox_and_account, isolated_db):
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_sent(cur, account_id, pmid="s1",
                     to_email="bob@partner.com", to_name="Bob")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "bob"})
    assert resp.status_code == 200, resp.text
    emails = {item["email"] for item in resp.json()}
    assert "bob@partner.com" in emails


def test_match_by_name_returns_address(test_client_base, setup_mailbox_and_account, isolated_db):
    # OR across (email, name): "amparo" matches the NAME even though the
    # email fragment is different.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="r1",
                         from_email="contacto@empresa.com", from_name="Amparo López")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "amparo"})
    assert resp.status_code == 200, resp.text
    emails = {item["email"] for item in resp.json()}
    assert "contacto@empresa.com" in emails


def test_accent_and_case_insensitive_match(test_client_base, setup_mailbox_and_account, isolated_db):
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="r1",
                         from_email="jose@example.com", from_name="José")

    # Query without the accent and lowercased must still match (unaccent+lower).
    resp = test_client_base.get(CONTACTS_URL, params={"q": "jos"})
    assert resp.status_code == 200, resp.text
    emails = {item["email"] for item in resp.json()}
    assert "jose@example.com" in emails


def test_spam_and_trash_addresses_are_excluded(test_client_base, setup_mailbox_and_account, isolated_db):
    # An address that exists ONLY in SPAM / TRASH must not be suggested.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="spam1",
                         from_email="spammer@spam.test", from_name="Spammer", box="SPAM")
        _insert_received(cur, account_id, pmid="trash1",
                         from_email="trashed@trash.test", from_name="Trashed", box="TRASH")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "spam"})
    assert resp.status_code == 200, resp.text
    assert "spammer@spam.test" not in {i["email"] for i in resp.json()}

    resp = test_client_base.get(CONTACTS_URL, params={"q": "trash"})
    assert resp.status_code == 200, resp.text
    assert "trashed@trash.test" not in {i["email"] for i in resp.json()}


def test_own_account_address_is_excluded(test_client_base, setup_mailbox_and_account, isolated_db):
    # A sent row whose ``to_email`` is the user's OWN account address must
    # not suggest the user back to themselves; a real recipient still does.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _set_account_email(cur, account_id, "owner@self.test")
        _insert_sent(cur, account_id, pmid="self1",
                     to_email="owner@self.test", to_name="Owner")
        _insert_sent(cur, account_id, pmid="real1",
                     to_email="external@partner.test", to_name="External")

    # Search on a fragment shared by both addresses' local part.
    resp = test_client_base.get(CONTACTS_URL, params={"q": "owner@self"})
    assert resp.status_code == 200, resp.text
    assert "owner@self.test" not in {i["email"] for i in resp.json()}

    resp = test_client_base.get(CONTACTS_URL, params={"q": "external"})
    assert resp.status_code == 200, resp.text
    assert "external@partner.test" in {i["email"] for i in resp.json()}


def test_duplicate_address_dedups_and_keeps_most_recent_name(
    test_client_base, setup_mailbox_and_account, isolated_db,
):
    # Same address seen twice with different names / timestamps → ONE entry
    # whose name is the most-recent non-empty one.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="dup-old",
                         from_email="carol@example.com", from_name="Carol Old",
                         received_at="2026-05-01T09:00:00+00:00")
        _insert_received(cur, account_id, pmid="dup-new",
                         from_email="carol@example.com", from_name="Carol New",
                         received_at="2026-05-10T09:00:00+00:00")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "carol"})
    assert resp.status_code == 200, resp.text
    carol = [i for i in resp.json() if i["email"] == "carol@example.com"]
    assert len(carol) == 1, "Dedup must collapse the two rows into one"
    assert carol[0]["name"] == "Carol New"


def test_aggregates_across_multiple_accounts_of_same_user(
    test_client_base, setup_mailbox_and_account, isolated_db,
):
    # Two accounts of the SAME user contribute to the same suggestion list.
    _mid, account_a = setup_mailbox_and_account(test_client_base, provider="gmail")
    _mid2, account_b = setup_mailbox_and_account(test_client_base, provider="outlook")
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_a, pmid="a1",
                         from_email="dave@example.com", from_name="Dave")
        _insert_received(cur, account_b, pmid="b1",
                         from_email="dawn@example.com", from_name="Dawn")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "da"})
    assert resp.status_code == 200, resp.text
    emails = {i["email"] for i in resp.json()}
    assert {"dave@example.com", "dawn@example.com"} <= emails


def test_addresses_of_other_users_are_not_suggested(
    test_client_base, setup_mailbox_and_account, isolated_db,
):
    # A foreign user's account with a matching address must never leak into
    # the authenticated user's (TEST_USER_ID) suggestions.
    _mid, _account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        cur.execute(
            "INSERT INTO users (user_id, auth_provider, provider_sub, email) "
            "VALUES (%s, %s, %s, %s)",
            (_OTHER_USER_ID, "google", "contacts-other-sub", "other@example.com"),
        )
        cur.execute(
            "INSERT INTO mailboxes (mailbox_id, display_name, owner_user_id) "
            "VALUES (%s, %s, %s)",
            (_OTHER_MAILBOX_ID, "Other MB", _OTHER_USER_ID),
        )
        cur.execute(
            "INSERT INTO accounts (account_id, mailbox_id, provider, display_label) "
            "VALUES (%s, %s, 'gmail', 'other-acc')",
            (_OTHER_ACCOUNT_ID, _OTHER_MAILBOX_ID),
        )
        _insert_received(cur, _OTHER_ACCOUNT_ID, pmid="foreign1",
                         from_email="foreigner@stranger.test", from_name="Foreigner")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "foreigner"})
    assert resp.status_code == 200, resp.text
    assert "foreigner@stranger.test" not in {i["email"] for i in resp.json()}


def test_limit_is_respected(test_client_base, setup_mailbox_and_account, isolated_db):
    # Insert more matching addresses than the requested limit and verify the
    # response is capped.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        for i in range(6):
            _insert_received(cur, account_id, pmid=f"lim{i}",
                             from_email=f"limit{i}@example.com", from_name=f"Limit {i}")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "limit", "limit": 3})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 3


def test_results_ordered_by_frequency_then_recency(
    test_client_base, setup_mailbox_and_account, isolated_db,
):
    # Pins the ORDER BY frequency DESC, last_seen DESC contract AND that the
    # limit keeps the TOP-ranked rows (not an arbitrary subset). All four
    # addresses share the "rank" fragment (via from_name) so q matches them
    # equally — only frequency/recency decides the order and the cut.
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        # frequency 3, oldest timestamp — frequency dominates recency.
        for i in range(3):
            _insert_received(cur, account_id, pmid=f"f3-{i}",
                             from_email="freq3@rank.test", from_name="Rank Contact",
                             received_at="2026-05-01T10:00:00+00:00")
        # frequency 2, most recent of the freq-2 pair.
        for i in range(2):
            _insert_received(cur, account_id, pmid=f"f2r-{i}",
                             from_email="freq2recent@rank.test", from_name="Rank Contact",
                             received_at="2026-05-20T10:00:00+00:00")
        # frequency 2, older → loses the recency tie-break to freq2recent.
        for i in range(2):
            _insert_received(cur, account_id, pmid=f"f2o-{i}",
                             from_email="freq2old@rank.test", from_name="Rank Contact",
                             received_at="2026-05-05T10:00:00+00:00")
        # frequency 1 but the MOST recent of all — must still be dropped by the
        # limit, proving frequency outranks recency.
        _insert_received(cur, account_id, pmid="f1",
                         from_email="freq1@rank.test", from_name="Rank Contact",
                         received_at="2026-05-25T10:00:00+00:00")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "rank", "limit": 3})
    assert resp.status_code == 200, resp.text
    ordered = [item["email"] for item in resp.json()]
    assert ordered == [
        "freq3@rank.test",
        "freq2recent@rank.test",
        "freq2old@rank.test",
    ]


# ---------------------------------------------------------------------------
# Contract — validation and empty-token behaviour
# ---------------------------------------------------------------------------


def test_single_char_query_is_rejected_422(test_client_base):
    resp = test_client_base.get(CONTACTS_URL, params={"q": "a"})
    assert resp.status_code == 422


def test_whitespace_only_query_returns_empty_list(test_client_base, setup_mailbox_and_account, isolated_db):
    # ``q`` passes ``min_length=2`` but tokenises to nothing → 200 with [].
    _mid, account_id = setup_mailbox_and_account(test_client_base)
    with isolated_db.cursor() as cur:
        _insert_received(cur, account_id, pmid="r1",
                         from_email="alice@example.com", from_name="Alice")

    resp = test_client_base.get(CONTACTS_URL, params={"q": "  "})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Error translation — both store calls, both branches (DB error vs unexpected)
# ---------------------------------------------------------------------------
# The service wraps two store calls (account lookup + suggestions query), each
# with a DatabaseError→translate (503 database_query_error) and a generic
# Exception→RecipientSuggestionsError (500) branch. These verify the actual
# HTTP status + error envelope produced end-to-end for all four branches.


def _raise_query_error(*_args, **_kwargs):
    raise QueryError("forced query failure")


def _raise_unexpected(*_args, **_kwargs):
    raise RuntimeError("forced unexpected failure")


def test_account_lookup_database_error_returns_503(test_client_base, monkeypatch):
    monkeypatch.setattr(
        contacts_service.account_store, "list_account_ids_by_user", _raise_query_error,
    )
    resp = test_client_base.get(CONTACTS_URL, params={"q": "am"})
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "database_query_error"


def test_account_lookup_unexpected_error_returns_500(test_client_base, monkeypatch):
    monkeypatch.setattr(
        contacts_service.account_store, "list_account_ids_by_user", _raise_unexpected,
    )
    resp = test_client_base.get(CONTACTS_URL, params={"q": "am"})
    assert resp.status_code == 500, resp.text
    assert resp.json()["error"]["code"] == "recipient_suggestions_error"


def test_suggestions_query_database_error_returns_503(
    test_client_base, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    # Owns ≥1 account so the service gets past the account lookup and reaches
    # the suggestions store, where the DB error is raised.
    setup_mailbox_and_account(test_client_base)
    monkeypatch.setattr(
        contacts_service.email_metadata_store, "list_recipient_suggestions", _raise_query_error,
    )
    resp = test_client_base.get(CONTACTS_URL, params={"q": "am"})
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "database_query_error"


def test_suggestions_query_unexpected_error_returns_500(
    test_client_base, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    setup_mailbox_and_account(test_client_base)
    monkeypatch.setattr(
        contacts_service.email_metadata_store, "list_recipient_suggestions", _raise_unexpected,
    )
    resp = test_client_base.get(CONTACTS_URL, params={"q": "am"})
    assert resp.status_code == 500, resp.text
    assert resp.json()["error"]["code"] == "recipient_suggestions_error"
