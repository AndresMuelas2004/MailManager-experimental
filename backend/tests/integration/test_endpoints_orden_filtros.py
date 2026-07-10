"""Tests de integración — chips de filtro rápido y ordenación del listado."""

from __future__ import annotations

import pytest

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL
from tests.integration.test_endpoints_lupa import _ids, _insert_operator_fixture_rows


# ------------------------------------------------------------------
# Emails — list emails: quick-filter chips (unread / has_attachment /
# favorite_only) and sort / sort_dir. Reuse the SAME operator fixture rows:
# the chips translate to the same is:unread / has:attachment / is:favorite
# operator clauses, and the controlled subjects/senders/dates let us assert
# exact ordering. (op-a: read, attach, "Job offer for you", LinkedIn, 04-10;
# op-b: unread, fav, "Weekly digest", GitHub, 04-20; op-c: SENT, "Factura".)
# ------------------------------------------------------------------

def test_list_emails_chip_unread(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # ``unread=true`` chip → only op-b (same result as ``q=is:unread`` but via
    # the dedicated chip param).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "unread": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == {"op-b"}
    assert body["total"] == 1


def test_list_emails_chip_has_attachment(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # ``has_attachment=true`` → only op-a (the single row with
    # has_attachments=TRUE).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "has_attachment": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == {"op-a"}
    assert body["total"] == 1


def test_list_emails_chip_favorite_only(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # ``favorite_only=true`` → only op-b (the favourite row). This is the plain
    # AND chip on the current box, distinct from the ``favorite`` anchor param.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, "favorite_only": "true"},
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-b"}


def test_list_emails_chips_combined_and(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # Two chips AND together: op-b is unread but has no attachment; op-a has an
    # attachment but is read → no row satisfies both.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "unread": "true",
            "has_attachment": "true",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == set()
    assert body["total"] == 0


def test_list_emails_chip_combined_with_search(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # A chip ANDs with free-text q: favorite_only=true + q="digest" → op-b
    # (favourite AND its subject contains "digest").
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "favorite_only": "true",
            "q": "digest",
        },
    )
    assert resp.status_code == 200
    assert _ids(resp.json()) == {"op-b"}


def test_list_emails_sort_subject_ascending(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # sort=subject&sort_dir=asc orders the two ALL_MAIL rows by normalised
    # subject: "job offer for you" < "weekly digest" → [op-a, op-b].
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "sort": "subject",
            "sort_dir": "asc",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [row["provider_message_id"] for row in body["items"]] == ["op-a", "op-b"]
    # Reordering must not change the size of the filtered set.
    assert body["total"] == 2


def test_list_emails_sort_sender_ascending(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # sort=sender&sort_dir=asc orders by from_name normalised: "github" (op-b)
    # < "linkedin" (op-a) → [op-b, op-a].
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "sort": "sender",
            "sort_dir": "asc",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [row["provider_message_id"] for row in body["items"]] == ["op-b", "op-a"]
    assert body["total"] == 2


def test_list_emails_sort_date_ascending_is_oldest_first(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # sort=date&sort_dir=asc reverses the default newest-first ordering:
    # op-a (2026-04-10) precedes op-b (2026-04-20).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "sort": "date",
            "sort_dir": "asc",
        },
    )
    assert resp.status_code == 200
    assert [row["provider_message_id"] for row in resp.json()["items"]] == ["op-a", "op-b"]


def test_list_emails_sort_date_desc_is_default_order(
    test_client, setup_mailbox_and_account, isolated_db,
):
    # The explicit default (date / desc) matches the omitted-params ordering:
    # newest first → op-b (04-20) then op-a (04-10).
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={
            "box": "ALL_MAIL",
            "account_id": account_id,
            "sort": "date",
            "sort_dir": "desc",
        },
    )
    assert resp.status_code == 200
    assert [row["provider_message_id"] for row in resp.json()["items"]] == ["op-b", "op-a"]


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "size"},
        {"sort_dir": "up"},
    ],
)
def test_list_emails_invalid_sort_params_return_422(
    test_client, setup_mailbox_and_account, isolated_db, params,
):
    # sort / sort_dir are router Literals → an out-of-whitelist value is
    # rejected by FastAPI with 422 (the default ``detail[]`` envelope), never
    # reaching the service.
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _insert_operator_fixture_rows(isolated_db, account_id)
    resp = test_client.get(
        f"{_MAILBOX_URL}/{mailbox_id}/emails",
        params={"box": "ALL_MAIL", "account_id": account_id, **params},
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()
