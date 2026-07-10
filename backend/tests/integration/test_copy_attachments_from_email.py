"""
Integration tests for the Forward attachment copy endpoint:
    - POST /mailboxes/{mid}/accounts/{aid}/drafts/{pdid}/attachments/copy-from-email

Gmail downloads + re-uploads (R-06); Outlook is a no-op because
``createForward`` already inherited the attachments server-side
(R-09). R-12 idempotency: a retry with the same source attachments
skips already-copied rows.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg2.extras

from api.services import drafts_service
from core.email import EmailManager
from core.email.email_client import AttachmentBinary
from tests.integration.conftest import (
    MAILBOX_URL as _MAILBOX_URL,
    patch_drafts_build_manager,
)
from tests.shared.email_fakes import FakeEmailClient


def _copy_url(mailbox_id: str, account_id: str, draft_id: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/drafts/"
        f"{draft_id}/attachments/copy-from-email"
    )


def _seed_draft(isolated_db, *, account_id: str, draft_id: str) -> None:
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO drafts (
                provider_draft_id, account_id, to_recipients, cc_recipients,
                bcc_recipients, subject, body
            )
            VALUES (
                %(pid)s, %(aid)s::uuid, %(empty)s, %(empty)s,
                %(empty)s, 'Fwd: Hello', 'body'
            )
            """,
            {"pid": draft_id, "aid": account_id, "empty": []},
        )


def _seed_source_email_with_attachment(
    isolated_db,
    *,
    account_id: str,
    provider_message_id: str,
    attachment_id: str,
    filename: str = "src.pdf",
    blob: bytes | None = b"BLOB-BYTES",
    is_inline: bool = False,
    unavailable_at: str | None = None,
) -> None:
    """Seed an email_metadata row + email_attachments row, and
    optionally seed the cached blob in email_attachment_blobs."""
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO email_metadata
                (provider_message_id, account_id, thread_id, from_email,
                 from_name, subject, received_at, is_read, box,
                 has_attachments)
            VALUES (%(pmid)s, %(aid)s::uuid, 't', 'a@b.com', 'A', 's',
                    '2026-05-19T00:00:00+00:00', false, 'ALL_MAIL', true)
            """,
            {"pmid": provider_message_id, "aid": account_id},
        )
        cur.execute(
            """
            INSERT INTO email_attachments
                (attachment_id, account_id, provider_message_id, part_id,
                 provider_attachment_id, filename, mime_type, size,
                 content_id, is_inline, position, unavailable_at,
                 last_accessed_at)
            VALUES (%(aid_att)s::uuid, %(aid)s::uuid, %(pmid)s, '1',
                    NULL, %(fn)s, 'application/pdf', %(sz)s,
                    NULL, %(inline)s, 0, %(unav)s, now())
            """,
            {
                "aid_att": attachment_id,
                "aid": account_id,
                "pmid": provider_message_id,
                "fn": filename,
                "sz": len(blob or b""),
                "inline": is_inline,
                "unav": unavailable_at,
            },
        )
        if blob is not None and unavailable_at is None:
            cur.execute(
                """
                INSERT INTO email_attachment_blobs
                    (attachment_id, blob, blob_storage_kind, blob_ref)
                VALUES (%(aid)s::uuid, %(blob)s, 'db', NULL)
                """,
                {"aid": attachment_id, "blob": psycopg2.Binary(blob)},
            )


def _patch_fake_manager(monkeypatch, **fake_kwargs):
    captured: list[FakeEmailClient] = []

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id") or "")
            aid = str(acc.get("account_id") or "")
            label = f"{mid}__{aid}"
            client = FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **fake_kwargs,
            )
            captured.append(client)
            manager.add_client(client)
        return manager

    patch_drafts_build_manager(monkeypatch, _build)
    return captured


def test_outlook_is_a_noop_returns_current_chip_list(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """For Outlook drafts the endpoint short-circuits — createForward
    already inherited the attachments at draft creation."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "outlook")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-1")

    # Source account in a separate Gmail mailbox owned by the same user.
    src_mbx_resp = test_client.post(_MAILBOX_URL, json={"display_name": "Src"})
    src_mbx = src_mbx_resp.json()["mailbox_id"]
    src_acc_resp = test_client.post(
        f"{_MAILBOX_URL}/{src_mbx}/accounts",
        json={"provider": "gmail", "display_label": "src"},
    )
    src_acc_id = src_acc_resp.json()["account_id"]
    src_att_id = str(uuid4())
    _seed_source_email_with_attachment(
        isolated_db, account_id=src_acc_id, provider_message_id="src-msg",
        attachment_id=src_att_id,
    )
    _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-1"),
        json={
            "source_account_id": src_acc_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert body["skipped"] == []

    # No draft_attachments row was inserted for this draft.
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM draft_attachments "
            "WHERE account_id = %s::uuid AND provider_draft_id = %s",
            (account_id, "drf-1"),
        )
        assert cur.fetchone()[0] == 0


def test_gmail_cached_blob_copy_persists_with_source_tracking(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Cache hit: the cached blob is reused (no provider call) and the
    new draft_attachments row carries source_account_id + source_attachment_id
    for R-12 idempotency."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-2")
    src_att_id = str(uuid4())
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=src_att_id, blob=b"CACHED-BLOB",
    )
    captured = _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-2"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 1
    assert body["skipped"] == []

    # Provider was NOT called — cache hit.
    assert all(not client.fetch_attachment_binary_calls for client in captured)

    # The new draft_attachments row carries R-12 source columns and
    # the cached blob.
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT filename, source_account_id, source_attachment_id, blob "
            "FROM draft_attachments "
            "WHERE account_id = %s::uuid AND provider_draft_id = %s",
            (account_id, "drf-2"),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["filename"] == "src.pdf"
    assert str(row["source_account_id"]) == account_id
    assert str(row["source_attachment_id"]) == src_att_id
    assert bytes(row["blob"]) == b"CACHED-BLOB"


def test_gmail_cache_miss_falls_back_to_provider_download(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-3")
    src_att_id = str(uuid4())
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=src_att_id, blob=None,  # No cached blob.
    )

    # Manually insert the metadata row without a blob row.
    fake_bin = AttachmentBinary(
        mime_type="application/pdf",
        filename="src.pdf",
        data=b"PROVIDER-BYTES",
        size=len(b"PROVIDER-BYTES"),
    )
    captured = _patch_fake_manager(
        monkeypatch, fetch_attachment_binary_return=fake_bin,
    )

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-3"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["copied_count"] == 1

    # Provider was invoked once for the binary.
    assert sum(len(c.fetch_attachment_binary_calls) for c in captured) == 1

    # The downloaded blob is now in the draft_attachments row AND has
    # been written back to email_attachment_blobs for future cache hits.
    with isolated_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT blob FROM draft_attachments "
            "WHERE account_id = %s::uuid AND provider_draft_id = %s",
            (account_id, "drf-3"),
        )
        row = cur.fetchone()
        assert row is not None
        assert bytes(row["blob"]) == b"PROVIDER-BYTES"
        cur.execute(
            "SELECT blob FROM email_attachment_blobs "
            "WHERE attachment_id = %s::uuid",
            (src_att_id,),
        )
        cached = cur.fetchone()
        assert cached is not None


def test_idempotency_skips_already_copied_attachment(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """R-12: retrying with the same source attachment skips the row
    that already exists in this draft (matched via source_attachment_id)."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-4")
    src_att_id = str(uuid4())
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=src_att_id, blob=b"CACHED",
    )
    _patch_fake_manager(monkeypatch)

    # First copy: lands the row.
    first = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-4"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert first.status_code == 200
    assert first.json()["copied_count"] == 1

    # Second copy: same source. Must skip.
    second = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-4"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert second.status_code == 200
    body = second.json()
    assert body["copied_count"] == 0
    assert any(s.get("reason") == "already_copied" for s in body["skipped"])

    # Only one draft_attachment row exists.
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM draft_attachments "
            "WHERE account_id = %s::uuid AND provider_draft_id = %s",
            (account_id, "drf-4"),
        )
        assert cur.fetchone()[0] == 1


def test_unavailable_source_reported_as_skipped(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A previously-stamped ``unavailable_at`` (D-17) short-circuits to
    the structured ``skipped`` entry instead of paying for a guaranteed
    404 from the provider."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-5")
    src_att_id = str(uuid4())
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=src_att_id,
        blob=None,
        unavailable_at="2026-05-23T14:00:00Z",
    )
    captured = _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-5"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert any(
        s.get("reason") == "unavailable_at_source"
        for s in body["skipped"]
    )
    # Provider was NOT called.
    assert all(not c.fetch_attachment_binary_calls for c in captured)


def test_inline_attachments_filtered_out(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """Inline parts (HTML-embedded images) are not chips — D-13."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-6")
    _seed_source_email_with_attachment(
        isolated_db,
        account_id=account_id,
        provider_message_id="src-msg",
        attachment_id=str(uuid4()),
        filename="inline-logo.png",
        is_inline=True,
        blob=b"PNG",
    )
    _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-6"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["copied_count"] == 0
    # No row inserted.
    with isolated_db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM draft_attachments "
            "WHERE account_id = %s::uuid AND provider_draft_id = %s",
            (account_id, "drf-6"),
        )
        assert cur.fetchone()[0] == 0


def test_unknown_source_account_returns_404(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """D-22 anti-leak: a missing/foreign source account collapses to 404."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-7")
    _patch_fake_manager(monkeypatch)
    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-7"),
        json={
            "source_account_id": str(uuid4()),  # not owned by user
            "source_provider_message_id": "src-msg",
        },
    )
    assert resp.status_code == 404


def test_missing_source_email_returns_404(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A valid source account but missing source email row → 404."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-8")
    _patch_fake_manager(monkeypatch)
    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-8"),
        json={
            "source_account_id": account_id,
            "source_provider_message_id": "never-existed",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "email_not_found"


def _seed_draft_attachment_chip(
    isolated_db, *, account_id: str, provider_draft_id: str, position: int, size: int,
) -> None:
    """Seed a chip in the TARGET draft to reach the D-02/D-03 caps without
    buffering real bytes (the ``size`` column drives the cap checks)."""
    with isolated_db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO draft_attachments
                (draft_attachment_id, account_id, provider_draft_id, filename,
                 mime_type, size, content_id, is_inline, position, blob,
                 blob_storage_kind, blob_ref, provider_attachment_id)
            VALUES (%s, %s::uuid, %s, %s, 'application/pdf', %s, NULL, false, %s,
                    %s, 'db', NULL, NULL)
            """,
            (
                str(uuid4()), account_id, provider_draft_id, f"chip-{position}.pdf",
                size, position, psycopg2.Binary(b"x"),
            ),
        )


def test_provider_download_failure_reported_as_skipped(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """A provider error during the per-attachment download surfaces as a
    structured ``skipped`` entry (200) — it must NOT abort the whole batch
    with a 502 (the regression this test guards against)."""
    from core.email.errors import EmailExternalAPIError

    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-pf")
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=str(uuid4()), blob=None,  # cache miss → provider fetch
    )
    _patch_fake_manager(
        monkeypatch,
        fetch_attachment_binary_exc=EmailExternalAPIError("provider down"),
    )

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-pf"),
        json={"source_account_id": account_id, "source_provider_message_id": "src-msg"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert any(s.get("reason") == "provider_unavailable" for s in body["skipped"])


def test_count_cap_reports_skipped(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """D-03: a draft already holding 25 chips skips a further copy."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-cc")
    for i in range(25):
        _seed_draft_attachment_chip(
            isolated_db, account_id=account_id, provider_draft_id="drf-cc",
            position=i, size=1,
        )
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=str(uuid4()), blob=b"CACHED",
    )
    _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-cc"),
        json={"source_account_id": account_id, "source_provider_message_id": "src-msg"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert any(s.get("reason") == "attachment_limit_exceeded" for s in body["skipped"])


def test_cumulative_size_cap_reports_skipped(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """D-02: a draft already holding ~25 MB skips a further copy."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-sz")
    _seed_draft_attachment_chip(
        isolated_db, account_id=account_id, provider_draft_id="drf-sz",
        position=0, size=25 * 1024 * 1024,
    )
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=str(uuid4()), blob=b"CACHED",
    )
    _patch_fake_manager(monkeypatch)

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-sz"),
        json={"source_account_id": account_id, "source_provider_message_id": "src-msg"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert any(s.get("reason") == "message_size_exceeded" for s in body["skipped"])


def test_blob_lookup_failure_reported_as_skipped(
    test_client, setup_mailbox_and_account, isolated_db, monkeypatch,
):
    """An unexpected (non-DatabaseError) failure reading the cached blob
    surfaces as ``blob_lookup_failed`` instead of aborting the batch."""
    mailbox_id, account_id = setup_mailbox_and_account(test_client, "gmail")
    _seed_draft(isolated_db, account_id=account_id, draft_id="drf-bl")
    _seed_source_email_with_attachment(
        isolated_db, account_id=account_id, provider_message_id="src-msg",
        attachment_id=str(uuid4()), blob=b"CACHED",
    )
    _patch_fake_manager(monkeypatch)
    monkeypatch.setattr(
        drafts_service.email_attachment_store, "get_blob",
        lambda _aid: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    resp = test_client.post(
        _copy_url(mailbox_id, account_id, "drf-bl"),
        json={"source_account_id": account_id, "source_provider_message_id": "src-msg"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["copied_count"] == 0
    assert any(s.get("reason") == "blob_lookup_failed" for s in body["skipped"])
