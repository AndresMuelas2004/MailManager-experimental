"""
Integration tests for the virtual (fake) mailbox surface.

Covers CRUD and the filtered email listing. Provider clients are NOT
invoked by this surface, so the standard ``test_client`` fixture (which
wires fake clients into the manager) is reused only to inherit the
common monkeypatches; the tests do not exercise the manager itself.

A virtual mailbox is a flat list of ``account_ids`` plus a filter (no
``scope_kind`` indirection — collapsed by migration 0032).
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


def _reparent_seeded_user(isolated_db, owner_user_id: str) -> None:
    """Make seeded gmail+outlook mailboxes owned by ``owner_user_id`` so the
    standard ``test_client`` (authenticated as TEST_USER_ID) can see them."""
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE mailboxes SET owner_user_id = %s "
            "WHERE mailbox_id IN (%s, %s)",
            (owner_user_id, _SEEDED_GMAIL_MAILBOX, _SEEDED_OUTLOOK_MAILBOX),
        )


# ---------------------------------------------------------------------------
# CRUD — happy path and validation
# ---------------------------------------------------------------------------


def test_list_empty_returns_empty_array(test_client):
    resp = test_client.get(VMB_URL)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_only_owned_vmboxes(test_client, isolated_db):
    # Owner-scoping of GET /virtual-mailboxes: two vmboxes owned by the
    # authenticated user must be returned, and one inserted under a DIFFERENT
    # owner must be excluded. The empty-list test alone never exercises the
    # populated owner-scoped path, so a cross-user leak in
    # LIST_VIRTUAL_MAILBOXES_BY_OWNER's ``WHERE owner_user_id = %s`` would go
    # unnoticed.
    import uuid
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    first = test_client.post(VMB_URL, json={
        "display_name": "Owned A",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    assert first.status_code == 201, first.text
    second = test_client.post(VMB_URL, json={
        "display_name": "Owned B",
        "account_ids": [_SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {},
    })
    assert second.status_code == 201, second.text
    owned_ids = {
        first.json()["virtual_mailbox_id"],
        second.json()["virtual_mailbox_id"],
    }

    # A vmbox owned by a different user — must never surface in this list.
    foreign_id = str(uuid.uuid4())
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO virtual_mailboxes
                (virtual_mailbox_id, owner_user_id, display_name,
                 scope_payload, filter_payload)
            VALUES (%s, '11111111-1111-4000-a000-111111111111',
                    'foreign', '{"account_ids":[]}'::jsonb, '{}'::jsonb)
            """,
            (foreign_id,),
        )

    resp = test_client.get(VMB_URL)
    assert resp.status_code == 200, resp.text
    returned_ids = {row["virtual_mailbox_id"] for row in resp.json()}
    assert returned_ids == owned_ids
    assert foreign_id not in returned_ids


def test_create_happy_path(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    payload = {
        "display_name": "Newsletters",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": "newsletter"},
    }
    resp = test_client.post(VMB_URL, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["display_name"] == "Newsletters"
    assert body["account_ids"] == [_SEEDED_GMAIL_ACCOUNT]
    assert body["filter_payload"] == {"subject_contains": "newsletter"}
    assert "virtual_mailbox_id" in body


def test_create_rejects_empty_account_ids(test_client):
    """A virtual mailbox without any account is meaningless. The Pydantic
    ``min_length=1`` on the ``account_ids`` field surfaces this at the
    schema boundary so the API returns 422 instead of silently saving an
    unusable row."""
    resp = test_client.post(VMB_URL, json={
        "display_name": "Empty",
        "account_ids": [],
        "filter_payload": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(d.get("loc", [])[-1] == "account_ids" for d in detail)


def test_create_rejects_extra_top_level_key(test_client):
    """``VirtualMailboxCreate`` carries ``extra="forbid"`` so a typo'd
    top-level key (e.g. ``accountIds`` in camelCase) collapses to 422
    instead of being silently dropped and persisting an empty list."""
    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-extra-top-key",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
        "accountIds": ["noise"],
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "accountIds"
        for d in detail
    )


def test_create_rejects_unknown_filter_payload_key(test_client, isolated_db):
    """``filter_payload`` is a closed whitelist (repository_guide.md).

    Unknown keys must surface as 422 at the schema boundary, not be
    silently discarded by the repository's ``_EXTRA_FILTER_BUILDERS``
    lookup — otherwise typos hide from the user.
    """
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "Unknown filter",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"unknown_filter": "x"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "unknown_filter"
        for d in detail
    )


def test_create_rejects_from_domain_filter(test_client, isolated_db):
    """``from_domain`` was removed in the same change that collapsed
    ``scope_kind``. The filter never made sense for an account-scoped
    listing once exact ``from_email`` covered the actual use case.
    Sending it now must collapse to 422 to surface the deprecation
    instead of silently ignoring it."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "Old domain filter",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"from_domain": "example.com"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "from_domain"
        for d in detail
    )


def test_create_rejects_whitespace_only_display_name(test_client, isolated_db):
    """``display_name`` collapses to '' after strip ⇒ violates min_length=1.

    Without the schema-side strip the row would be persisted with an
    empty name (length 3 passes ``min_length=1``) and the service's
    own ``.strip()`` would silently turn it into ``''``.
    """
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "   ",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "display_name"
        for d in detail
    )


def test_create_strips_padded_display_name(test_client, isolated_db):
    """Leading / trailing whitespace is stripped before persisting."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "   Padded   ",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "Padded"


def test_update_rejects_unknown_filter_payload_key(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Base",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Base",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"from_email": "a@b.com", "bogus": 1},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "extra_forbidden"
        and d.get("loc", [])[-1] == "bogus"
        for d in detail
    )


def test_create_rejects_unowned_account(test_client, setup_mailbox_and_account):
    # Create a mailbox/account owned by the test user.
    _mailbox, account_id = setup_mailbox_and_account(test_client, "gmail")
    # The seeded gmail account belongs to a different user (migration 0010).
    resp = test_client.post(VMB_URL, json={
        "display_name": "Mix",
        "account_ids": [account_id, _SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_update_replaces_all_fields(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Original",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Renamed",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {"is_favorite": True},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Renamed"
    assert set(body["account_ids"]) == {_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT}
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
                 scope_payload, filter_payload)
            VALUES (%s, '11111111-1111-4000-a000-111111111111',
                    'foreign', '{"account_ids":[]}'::jsonb, '{}'::jsonb)
            """,
            (other_vmb_id,),
        )
    resp = test_client.get(f"{VMB_URL}/{other_vmb_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "virtual_mailbox_not_found"


def test_delete_removes_record(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "X",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
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


def test_emails_for_virtual_mailbox_aggregates_every_listed_account(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Everything",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    # Default filter (no explicit box, no box_not_in) excludes TRASH/SPAM.
    boxes = {row["box"] for row in rows}
    assert "TRASH" not in boxes
    assert "SPAM" not in boxes
    # ALL_MAIL rows from both providers must surface.
    account_ids = {row["account_id"] for row in rows}
    assert _SEEDED_GMAIL_ACCOUNT in account_ids
    assert _SEEDED_OUTLOOK_ACCOUNT in account_ids
    # Each row carries its real mailbox_id (derived from the JOIN on
    # ``accounts``). The frontend uses it to open / favourite / move
    # the right email — when aggregating across mailboxes, using the
    # route's ``mailbox_id`` would 404 with ``account_not_found`` on
    # the open path.
    mailbox_ids = {row["mailbox_id"] for row in rows}
    assert _SEEDED_GMAIL_MAILBOX in mailbox_ids
    assert _SEEDED_OUTLOOK_MAILBOX in mailbox_ids
    for row in rows:
        if row["account_id"] == _SEEDED_GMAIL_ACCOUNT:
            assert row["mailbox_id"] == _SEEDED_GMAIL_MAILBOX
        elif row["account_id"] == _SEEDED_OUTLOOK_ACCOUNT:
            assert row["mailbox_id"] == _SEEDED_OUTLOOK_MAILBOX


def test_emails_for_virtual_mailbox_filter_by_from_email(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Devops alerts",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"from_email": "jack@devops.net"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows  # seeded gmail data contains @devops.net entries
    assert all(row["from_email"].lower() == "jack@devops.net" for row in rows)


def test_emails_for_virtual_mailbox_filter_by_is_favorite(test_client, isolated_db):
    # The virtual listing now ALWAYS groups by thread (conversation view), so
    # the two favourites — which share thread_id 'thread-gm-001' in the seed —
    # collapse into ONE thread row. The is_favorite filter still applies BEFORE
    # grouping; total counts threads, not messages.
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
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"is_favorite": True},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    body = resp.json()
    rows = body["items"]
    # One thread row (the two favourites belong to the same thread).
    assert len(rows) == 1
    row = rows[0]
    assert row["thread_id"] == "thread-gm-001"
    # Representative is the most-recent favourite of the thread; both members
    # are favourites, so the aggregated row is favourite and counts 2.
    assert row["provider_message_id"] == "gmail-allmail-002"
    assert row["is_favorite"] is True
    assert row["thread_message_count"] == 2
    # COUNT(DISTINCT thread_key) over the favourite-filtered set → 1 thread.
    assert body["total"] == 1


def test_emails_for_virtual_mailbox_explicit_box_overrides_default(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Trash view",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"box": "TRASH"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows  # seeded data has TRASH rows for the gmail account
    assert all(row["box"] == "TRASH" for row in rows)


def test_emails_for_virtual_mailbox_filters_to_listed_account_subset(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Outlook only",
        "account_ids": [_SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows
    assert all(row["account_id"] == _SEEDED_OUTLOOK_ACCOUNT for row in rows)


def test_emails_for_virtual_mailbox_search_query_combines_with_filter(
    test_client, isolated_db,
):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Sprint planning",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "sprint"})
    assert resp.status_code == 200
    rows = resp.json()["items"]
    # Search tokens AND-combined with the (empty) filter — must still
    # return only matching rows.
    assert all("sprint" in (row["subject"] or "").lower() for row in rows)


def test_emails_for_vmbox_operator_ands_with_saved_filter(test_client, isolated_db):
    # A lupa operator from q ANDs with a SAVED virtual-mailbox filter. Saved
    # subject_contains="sprint" matches the two seeded sprint rows (019 Rachel,
    # 020 Sam); from:rachel in q narrows to gmail-allmail-019 only.
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Sprint by sender",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": "sprint"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "from:rachel"})
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert {r["provider_message_id"] for r in rows} == {"gmail-allmail-019"}


def test_emails_for_vmbox_in_intersects_compatible_box(test_client, isolated_db):
    # Default-exclusion vmbox (no box) → in:sent is compatible and narrows the
    # listing to SENT rows only.
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "All Gmail",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "in:sent"})
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows, "in:sent must surface the seeded SENT rows"
    assert all(r["box"] == "SENT" for r in rows)


def test_emails_for_vmbox_in_excluded_box_returns_empty(test_client, isolated_db):
    # Default-exclusion vmbox excludes TRASH/SPAM; in:trash asks for an excluded
    # box → empty page (the intersection is naturally empty).
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "All Gmail 2",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "in:trash"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_emails_for_vmbox_saved_is_read_contradicts_lupa_is_unread(test_client, isolated_db):
    # Saved is_read=True contradicts q is:unread → two incompatible AND clauses
    # → empty page. (Seed sprint rows: 019 read, 020 unread; the saved filter
    # keeps only read rows, the lupa keeps only unread → intersection empty.)
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Read sprint",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": "sprint", "is_read": True},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"q": "is:unread"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_filter_by_box_not_in_excludes_only_listed_box(test_client, isolated_db):
    """``box_not_in=['SPAM']`` excludes SPAM but keeps TRASH (and
    ALL_MAIL, SENT). The previous default-exclusion branch ate both —
    only the explicit branch honours per-box opt-out."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Exclude only SPAM",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"box_not_in": ["SPAM"]},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    rows = test_client.get(f"{VMB_URL}/{vmb_id}/emails").json()["items"]
    boxes = {r["box"] for r in rows}
    assert "SPAM" not in boxes
    assert "TRASH" in boxes
    assert "ALL_MAIL" in boxes


def test_filter_by_empty_box_not_in_includes_trash_and_spam(test_client, isolated_db):
    """``box_not_in=[]`` means "exclude nothing" — TRASH and SPAM must
    surface. A refactor that switches the guard to a truthiness check
    (``if box_not_in:``) would collapse the empty list to the default
    exclusion and reverse the caller's intent."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Include everything",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"box_not_in": []},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    rows = test_client.get(f"{VMB_URL}/{vmb_id}/emails").json()["items"]
    boxes = {r["box"] for r in rows}
    assert "TRASH" in boxes
    assert "SPAM" in boxes


def test_listing_dedups_same_provider_message_id_across_accounts(test_client, isolated_db):
    """Two ``account_id``s sharing the same Gmail / Outlook OAuth produce
    two ``email_metadata`` rows with the same ``provider_message_id``
    (composite PK is ``(provider_message_id, account_id)``). The virtual
    mailbox listing must collapse them in SQL via
    ``LIST_FILTERED_DISTINCT`` (``DISTINCT ON (provider_message_id)`` with
    the ``btrim(coalesce(to_email,''))<>''`` completeness tie-break) and
    report ``total=1`` via ``COUNT(DISTINCT provider_message_id)``. The
    winner must be the row with the real ``to_email`` (Gmail, 10:00), not
    the empty Outlook one (09:00). This pins the SQL dedup end-to-end:
    real PostgreSQL returns both rows, the DISTINCT collapses them, and
    the count does NOT over-count to 2."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, to_email, to_name)
            VALUES ('shared-dup-001', %s, NULL, 's@x.com', 'S',
                    'shared dup test', '2026-05-10T10:00:00+00:00',
                    FALSE, 'ALL_MAIL', 'real@x.com', 'Real')
            """,
            (_SEEDED_GMAIL_ACCOUNT,),
        )
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box, to_email, to_name)
            VALUES ('shared-dup-001', %s, NULL, 's@x.com', 'S',
                    'shared dup test', '2026-05-10T09:00:00+00:00',
                    FALSE, 'ALL_MAIL', '', '')
            """,
            (_SEEDED_OUTLOOK_ACCOUNT,),
        )

    create = test_client.post(VMB_URL, json={
        "display_name": "Dedup test",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {"subject_contains": "shared dup test"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    body = test_client.get(f"{VMB_URL}/{vmb_id}/emails").json()
    rows = body["items"]
    dup_rows = [r for r in rows if r["provider_message_id"] == "shared-dup-001"]
    assert len(dup_rows) == 1, "Dedup must collapse the two rows into one"
    assert dup_rows[0]["to_email"] == "real@x.com"
    assert dup_rows[0]["account_id"] == _SEEDED_GMAIL_ACCOUNT
    # COUNT(DISTINCT provider_message_id) must NOT over-count the duplicate.
    assert body["total"] == 1


def test_listing_drops_account_ids_no_longer_owned(test_client, isolated_db):
    """The vmbox persisted two account_ids but the user no longer owns
    one of them (the underlying mailbox was reparented to another user).
    The listing must keep working with only the surviving subset — no
    404, no 500, no leaked rows from the foreign account."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Mixed",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT, _SEEDED_OUTLOOK_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    # Reparent the Outlook mailbox to a different user — the Outlook
    # account is no longer owned by TEST_USER_ID.
    with isolated_db.cursor() as cur:
        cur.execute(
            "UPDATE mailboxes SET owner_user_id = %s WHERE mailbox_id = %s",
            ("11111111-1111-4000-a000-111111111111", _SEEDED_OUTLOOK_MAILBOX),
        )

    resp = test_client.get(f"{VMB_URL}/{vmb_id}/emails")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert rows
    assert all(row["account_id"] == _SEEDED_GMAIL_ACCOUNT for row in rows)


# ---------------------------------------------------------------------------
# Filter payload — empty-string criteria are 422 (not silent "match all")
# ---------------------------------------------------------------------------


def test_create_rejects_empty_subject_contains(test_client, isolated_db):
    """``subject_contains=""`` used to produce ``ILIKE '%%'`` matching every
    row, turning "filter by nothing" into a silent full-inbox dump
    indistinguishable from "no filter at all". ``min_length=1`` now
    rejects it at the schema boundary so the user gets explicit feedback.
    """
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-empty-subject",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": ""},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "subject_contains"
        for d in detail
    )


def test_create_rejects_empty_from_email(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "Bug-empty-from-email",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"from_email": ""},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(
        d.get("type") == "string_too_short"
        and d.get("loc", [])[-1] == "from_email"
        for d in detail
    )


def test_update_rejects_empty_subject_contains(test_client, isolated_db):
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Has filter",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": "sueldos"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Has filter",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": ""},
    })
    assert resp.status_code == 422


def test_create_rejects_box_and_box_not_in_together(test_client, isolated_db):
    """``VirtualMailboxFilterPayload._validate_box_exclusivity`` rejects
    payloads that set both ``box`` and a non-empty ``box_not_in``. Without
    this guard the repository would emit two ``AND box = X`` predicates
    simultaneously and silently return zero rows — a perpetually-empty
    bandeja indistinguishable from "no matches"."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    resp = test_client.post(VMB_URL, json={
        "display_name": "Mutex test",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"box": "SENT", "box_not_in": ["TRASH"]},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    messages = " ".join(d.get("msg", "") for d in detail)
    assert "box" in messages and "box_not_in" in messages


# ---------------------------------------------------------------------------
# Race condition: UPDATE / DELETE after the row has been removed
# ---------------------------------------------------------------------------


def test_patch_after_delete_returns_404_not_500(test_client, isolated_db):
    """The vmbox previously surfaced this race as a 500 because the repo
    raised ``QueryError("Virtual mailbox row to update not found.")`` which
    fell through to the generic ``VirtualMailboxOperationError`` handler
    (500). The fix makes the repo return ``None`` on no-match and the
    service translate that to ``VirtualMailboxNotFound`` (404), keeping
    the contract aligned with GET/DELETE.
    """
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Race target",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    # Concurrent delete (simulated by deleting then patching same id)
    assert test_client.delete(f"{VMB_URL}/{vmb_id}").status_code == 200

    resp = test_client.patch(f"{VMB_URL}/{vmb_id}", json={
        "display_name": "Should not land",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
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
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {"subject_contains": "tie subject"},
    })
    vmb_id = create.json()["virtual_mailbox_id"]

    p1_json = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"limit": 2, "offset": 0}).json()
    p2_json = test_client.get(f"{VMB_URL}/{vmb_id}/emails", params={"limit": 2, "offset": 2}).json()
    p1 = p1_json["items"]
    p2 = p2_json["items"]

    # total counts all 4 inserted rows (single account, no cross-account
    # duplicate), stable across both pages.
    assert p1_json["total"] == p2_json["total"] == 4

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


def test_delete_after_delete_returns_404_not_silent_200(test_client, isolated_db):
    """A second DELETE must not silently report success — two concurrent
    deletes both replying 200 would hide the conflict from the caller."""
    from tests.integration.conftest import TEST_USER_ID
    _reparent_seeded_user(isolated_db, TEST_USER_ID)

    create = test_client.post(VMB_URL, json={
        "display_name": "Double delete",
        "account_ids": [_SEEDED_GMAIL_ACCOUNT],
        "filter_payload": {},
    })
    vmb_id = create.json()["virtual_mailbox_id"]
    assert test_client.delete(f"{VMB_URL}/{vmb_id}").status_code == 200
    resp = test_client.delete(f"{VMB_URL}/{vmb_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "virtual_mailbox_not_found"
