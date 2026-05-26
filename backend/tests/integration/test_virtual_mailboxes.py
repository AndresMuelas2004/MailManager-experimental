"""
Integration tests for the virtual (fake) mailbox surface.

Covers CRUD and the filtered email listing. Provider clients are NOT
invoked by this surface, so the standard ``test_client`` fixture (which
wires fake clients into the manager) is reused only to inherit the
common monkeypatches; the tests do not exercise the manager itself.
"""

from __future__ import annotations

import psycopg2.extras

from tests.integration.conftest import (
    SEEDED_GMAIL_ACCOUNT_ID as _SEEDED_GMAIL_ACCOUNT,
    SEEDED_GMAIL_MAILBOX_ID as _SEEDED_GMAIL_MAILBOX,
    SEEDED_OUTLOOK_ACCOUNT_ID as _SEEDED_OUTLOOK_ACCOUNT,
    SEEDED_OUTLOOK_MAILBOX_ID as _SEEDED_OUTLOOK_MAILBOX,
)


VMB_URL = "/virtual-mailboxes"


def test_list_empty_returns_empty_array(test_client):
    resp = test_client.get(VMB_URL)
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_scope_all_happy_path(test_client):
    payload = {
        "display_name": "Newsletters",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"subject_contains": "newsletter"},
    }
    resp = test_client.post(VMB_URL, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["display_name"] == "Newsletters"
    assert body["scope_kind"] == "all"
    assert body["filter_payload"] == {"subject_contains": "newsletter"}
    assert "virtual_mailbox_id" in body


def test_create_scope_mailbox_requires_mailbox_id(test_client):
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bad",
        "scope_kind": "mailbox",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert resp.status_code == 422  # Pydantic validation error


def test_create_rejects_unknown_filter_payload_key(test_client):
    """``filter_payload`` is a closed whitelist (repository_guide.md).

    Unknown keys must surface as 422 at the schema boundary, not be
    silently discarded by the repository's ``_EXTRA_FILTER_BUILDERS``
    lookup — otherwise typos hide from the user.
    """
    resp = test_client.post(VMB_URL, json={
        "display_name": "Unknown filter",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"unknown_filter": "x"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "unknown_filter"
        for d in detail
    )


def test_create_rejects_whitespace_only_display_name(test_client):
    """``display_name`` collapses to '' after strip ⇒ violates min_length=1.

    Without the schema-side strip the row would be persisted with an
    empty name (length 3 passes ``min_length=1``) and the service's
    own ``.strip()`` would silently turn it into ``''``.
    """
    resp = test_client.post(VMB_URL, json={
        "display_name": "   ",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "display_name"
        for d in detail
    )


def test_create_strips_padded_display_name(test_client):
    """Leading / trailing whitespace is stripped before persisting."""
    resp = test_client.post(VMB_URL, json={
        "display_name": "   Padded   ",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "Padded"


def test_update_rejects_unknown_filter_payload_key(test_client):
    create = test_client.post(VMB_URL, json={
        "display_name": "Base",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Base",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"from_email": "a@b.com", "bogus": 1},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "bogus"
        for d in detail
    )


def test_create_scope_accounts_rejects_unowned_account(
    test_client, setup_mailbox_and_account,
):
    # Create a mailbox/account owned by the test user.
    _mailbox, account_id = setup_mailbox_and_account(test_client, "gmail")
    # The seeded gmail account belongs to a different user (migration 0010).
    resp = test_client.post(VMB_URL, json={
        "display_name": "Mix",
        "scope_kind": "accounts",
        "scope_payload": {"account_ids": [account_id, _SEEDED_GMAIL_ACCOUNT]},
        "filter_payload": {},
    })
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_update_replaces_all_fields(test_client):
    create = test_client.post(VMB_URL, json={
        "display_name": "Original",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Renamed",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"is_favorite": True},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Renamed"
    assert body["filter_payload"] == {"is_favorite": True}


def test_get_foreign_record_collapses_to_404(test_client, isolated_db):
    # Insert a virtual mailbox owned by a different user, bypassing the
    # endpoint, so we can verify the ownership check.
    import uuid
    other_vmb_id = str(uuid.uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO virtual_mailboxes
                (virtual_mailbox_id, owner_user_id, display_name,
                 scope_kind, scope_payload, filter_payload)
            VALUES (%s, '11111111-1111-4000-a000-111111111111',
                    'foreign', 'all', '{}'::jsonb, '{}'::jsonb)
            """,
            (other_vmb_id,),
        )
    resp = test_client.get(f"{VMB_URL}/{other_vmb_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "virtual_mailbox_not_found"


def test_delete_removes_record(test_client):
    create = test_client.post(VMB_URL, json={
        "display_name": "X",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.delete(f"{VMB_URL}/{vmb_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert test_client.get(f"{VMB_URL}/{vmb_id}").status_code == 404


# ---------------------------------------------------------------------------
# Filtered listing
# ---------------------------------------------------------------------------


def _reparent_seeded_user(isolated_db, owner_user_id: str) -> None:
    """Make seeded gmail+outlook mailboxes owned by ``owner_user_id`` so the
    standard ``test_client`` (authenticated as TEST_USER_ID) can see them."""
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE mailboxes SET owner_user_id = %s "
            "WHERE mailbox_id IN (%s, %s)",
            (owner_user_id, _SEEDED_GMAIL_MAILBOX, _SEEDED_OUTLOOK_MAILBOX),
        )


def test_emails_for_virtual_mailbox_with_scope_all_aggregates_every_account(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Everything",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()
    # The seeded data has plenty of rows across boxes; the default
    # filter (no explicit box, no box_not_in) excludes TRASH/SPAM.
    boxes = {row["box"] for row in rows}
    assert "TRASH" not in boxes
    assert "SPAM" not in boxes
    # ALL_MAIL rows from both providers must surface.
    account_ids = {row["account_id"] for row in rows}
    assert _SEEDED_GMAIL_ACCOUNT in account_ids
    assert _SEEDED_OUTLOOK_ACCOUNT in account_ids


def test_emails_for_virtual_mailbox_filter_by_from_domain(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Devops alerts",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"from_domain": "devops.net"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows  # seeded gmail data contains @devops.net entries
    assert all(row["from_email"].lower().endswith("@devops.net") for row in rows)


def test_emails_for_virtual_mailbox_filter_by_is_favorite(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE email_metadata SET is_favorite = TRUE "
            "WHERE account_id = %s AND provider_message_id IN ('gmail-allmail-001','gmail-allmail-002')",
            (_SEEDED_GMAIL_ACCOUNT,),
        )

    create = test_client.post(VMB_URL, json={
        "display_name": "Favs",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"is_favorite": True},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()
    ids = {row["provider_message_id"] for row in rows}
    assert ids == {"gmail-allmail-001", "gmail-allmail-002"}


def test_emails_for_virtual_mailbox_explicit_box_overrides_default(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Trash view",
        "scope_kind": "mailbox",
        "scope_payload": {"mailbox_id": _SEEDED_GMAIL_MAILBOX},
        "filter_payload": {"box": "TRASH"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows  # seeded data has TRASH rows for the gmail account
    assert all(row["box"] == "TRASH" for row in rows)


def test_emails_for_virtual_mailbox_scope_accounts_filters_to_selected_subset(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Outlook only",
        "scope_kind": "accounts",
        "scope_payload": {"account_ids": [_SEEDED_OUTLOOK_ACCOUNT]},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    assert all(row["account_id"] == _SEEDED_OUTLOOK_ACCOUNT for row in rows)


def test_emails_for_virtual_mailbox_search_query_combines_with_filter(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Sprint planning",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "sprint"})
    assert resp.status_code == 200
    rows = resp.json()
    # Search tokens AND-combined with the (empty) filter — must still
    # return only matching rows.
    assert all("sprint" in (row["subject"] or "").lower() for row in rows)


# ---------------------------------------------------------------------------
# Filter payload — empty-string criteria are 422 (not silent "match all")
# ---------------------------------------------------------------------------


def test_create_rejects_empty_subject_contains(test_client):
    """``subject_contains=""`` used to produce ``ILIKE '%%'`` matching every
    row, turning "filter by nothing" into a silent full-inbox dump
    indistinguishable from "no filter at all". ``min_length=1`` now
    rejects it at the schema boundary so the user gets explicit feedback.
    """
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-empty-subject",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"subject_contains": ""},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "subject_contains"
        for d in detail
    )


def test_create_rejects_empty_from_email(test_client):
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-empty-from-email",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"from_email": ""},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "from_email"
        for d in detail
    )


def test_create_rejects_empty_from_domain(test_client):
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-empty-from-domain",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"from_domain": ""},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "from_domain"
        for d in detail
    )


def test_create_rejects_unknown_scope_payload_key(test_client):
    """``scope_payload`` was previously open-shape — Pydantic dropped
    unknown keys silently. That hid typos (e.g. ``accountIds`` camelCase
    vs the real ``account_ids``) and the user got a "valid" vmbox that
    returned 0 rows because the real scope key was never set. The fix
    makes ``VirtualMailboxScopePayload`` carry ``extra='forbid'``, the
    same closed-whitelist contract that ``filter_payload`` already had.
    """
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-extra-scope-key",
        "scope_kind": "all",
        "scope_payload": {"extra_unknown_key": "noise"},
        "filter_payload": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "extra_unknown_key"
        for d in detail
    )


def test_update_rejects_empty_subject_contains(test_client):
    create = test_client.post(VMB_URL, json={
        "display_name": "Has filter",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"subject_contains": "sueldos"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Has filter",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"subject_contains": ""},
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Race condition: UPDATE / DELETE after the row has been removed
# ---------------------------------------------------------------------------


def test_patch_after_delete_returns_404_not_500(test_client):
    """The vmbox previously surfaced this race as a 500 because the repo
    raised ``QueryError("Virtual mailbox row to update not found.")`` which
    fell through to the generic ``VirtualMailboxOperationError`` handler
    (500). The fix makes the repo return ``None`` on no-match and the
    service translate that to ``VirtualMailboxNotFound`` (404), keeping
    the contract aligned with GET/DELETE.
    """
    create = test_client.post(VMB_URL, json={
        "display_name": "Race target",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    # Concurrent delete (simulated by deleting then patching same id)
    assert test_client.delete(f"{VMB_URL}/{vmb_id}").status_code == 200

    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Should not land",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "virtual_mailbox_not_found"


def test_pagination_is_deterministic_with_timestamp_ties(
    test_client, isolated_db,
):
    """``ORDER BY received_at DESC`` alone leaves rows with identical
    timestamps in arbitrary order — under ``OFFSET``/``LIMIT`` pagination
    PostgreSQL could return the same row on two adjacent pages (overlap)
    or skip it entirely (gap). Real symptom: mass-sent newsletter batches
    that arrive within the same second cause silent dup/skip when the
    user scrolls through them.

    The fix adds ``account_id, provider_message_id`` as secondary keys
    (which together form the table's primary key) so the ordering is
    total and pagination is deterministic.
    """
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    # Insert 4 metadata rows in the seeded gmail account with the SAME
    # received_at — forces the tie-break path.
    same_ts = "2026-05-21T12:00:00+00:00"
    with isolated_db.cursor() as cur:
        for i in range(4):
            cur.execute(
                """
                INSERT INTO email_metadata
                    (provider_message_id, account_id, thread_id,
                     from_email, from_name, subject, received_at,
                     is_read, box)
                VALUES (%s, %s, NULL, 'a@x.com', 'A', %s, %s, FALSE, 'ALL_MAIL')
                ON CONFLICT DO NOTHING
                """,
                (f"qa_tie_{i}", _SEEDED_GMAIL_ACCOUNT, f"tie subject {i}", same_ts),
            )

    create = test_client.post(VMB_URL, json={
        "display_name": "Tie test",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {"subject_contains": "tie subject"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    p1 = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"limit": 2, "offset": 0}).json()
    p2 = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"limit": 2, "offset": 2}).json()

    ids_p1 = [(r["provider_message_id"], r["account_id"]) for r in p1]
    ids_p2 = [(r["provider_message_id"], r["account_id"]) for r in p2]

    # No overlap: every (provider_message_id, account_id) pair from p1
    # is absent from p2.
    overlap = [pair for pair in ids_p1 if pair in ids_p2]
    assert overlap == [], (
        f"Pagination returned the same row on two adjacent pages: {overlap}"
    )

    # And no gap: union covers all 4 rows we just inserted.
    union = set(ids_p1) | set(ids_p2)
    assert {f"qa_tie_{i}" for i in range(4)}.issubset({pair[0] for pair in union})


def test_delete_after_delete_returns_404_not_silent_200(test_client):
    """A second DELETE must not silently report success — two concurrent
    deletes both replying 200 would hide the conflict from the caller."""
    create = test_client.post(VMB_URL, json={
        "display_name": "Double delete",
        "scope_kind": "all",
        "scope_payload": {},
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    assert test_client.delete(f"{VMB_URL}/{vmb_id}").status_code == 200
    resp = test_client.delete(f"{VMB_URL}/{vmb_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "virtual_mailbox_not_found"
