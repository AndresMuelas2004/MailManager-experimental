"""
Integration tests for the draft attachment endpoints:

- ``POST   /mailboxes/{mid}/accounts/{aid}/drafts/{did}/attachments``
- ``DELETE /mailboxes/{mid}/accounts/{aid}/drafts/{did}/attachments/{aid}``

Both endpoints are local-only (D-07): the provider is NOT contacted.
The integration suite still uses the FakeEmailClient infrastructure to
keep the auth side-effect deterministic, but the assertions only touch
the attachment store.
"""

from __future__ import annotations

import io
import uuid

import psycopg2

from tests.integration.conftest import MAILBOX_URL as _MAILBOX_URL


def _seed_draft_attachment(cur, account_id, provider_draft_id, *, position, size):
    """Insert a draft_attachment row directly (bypasses the upload endpoint).

    Used to reach the count / cumulative caps without buffering real bytes:
    the ``size`` column drives the cap checks, so the blob can stay tiny even
    when ``size`` declares ~25 MB.
    """
    cur.execute(
        """
        INSERT INTO draft_attachments
            (draft_attachment_id, account_id, provider_draft_id, filename,
             mime_type, size, content_id, is_inline, position, blob,
             blob_storage_kind, blob_ref, provider_attachment_id)
        VALUES (%s, %s, %s, %s, 'application/pdf', %s, NULL, false, %s,
                %s, 'db', NULL, NULL)
        """,
        (
            str(uuid.uuid4()), account_id, provider_draft_id,
            f"seed-{position}.pdf", size, position, psycopg2.Binary(b"x"),
        ),
    )


def _attachments_url(mailbox_id: str, account_id: str, draft_id: str) -> str:
    return (
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}"
        f"/drafts/{draft_id}/attachments"
    )


def _attachment_item_url(
    mailbox_id: str, account_id: str, draft_id: str, attachment_id: str,
) -> str:
    return f"{_attachments_url(mailbox_id, account_id, draft_id)}/{attachment_id}"


def _create_draft(client, mailbox_id: str, account_id: str) -> str:
    """Create a draft and return its provider_draft_id."""
    response = client.post(
        f"{_MAILBOX_URL}/{mailbox_id}/accounts/{account_id}/drafts",
        json={
            "to_recipients": ["to@example.com"],
            "cc_recipients": [],
            "bcc_recipients": [],
            "subject": "S",
            "body": "B",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["provider_draft_id"]


# ── POST /attachments — upload ─────────────────────────────────────


class TestAddDraftAttachment:

    def test_happy_path_creates_attachment_row(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)

        response = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("report.pdf", io.BytesIO(b"PDF-bytes"), "application/pdf")},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["filename"] == "report.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["size"] == len(b"PDF-bytes")
        assert body["position"] == 0
        # Provider was NOT contacted — D-07 lazy push.
        assert body["provider_attachment_id"] is None

    def test_blocked_extension_rejected_with_400(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)

        response = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("malware.exe", io.BytesIO(b"x"), "application/octet-stream")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "attachment_blocked_extension"

    def test_oversized_single_file_rejected_with_413(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)

        oversized = b"\x00" * (25 * 1024 * 1024 + 1)
        response = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
        )
        # 25MB+1 is < 30MB so the multipart cap doesn't kick in; we hit
        # the per-attachment AttachmentTooLarge (HTTP 413).
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "attachment_too_large"

    def test_unknown_account_returns_404(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, _account_id = setup_mailbox_and_account(test_client)
        # Use a clearly-invalid uuid so the lookup returns None.
        ghost_account = "00000000-0000-0000-0000-000000000000"
        response = test_client.post(
            _attachments_url(mailbox_id, ghost_account, "draft-x"),
            files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
        )
        assert response.status_code == 404

    def test_unknown_draft_returns_404_with_draft_not_found_code(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        response = test_client.post(
            _attachments_url(mailbox_id, account_id, "ghost-draft-id"),
            files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "draft_not_found"

    def test_position_increments_per_attachment(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)

        responses = []
        for i in range(3):
            r = test_client.post(
                _attachments_url(mailbox_id, account_id, draft_id),
                files={
                    "file": (f"f{i}.pdf", io.BytesIO(f"data-{i}".encode()), "application/pdf"),
                },
            )
            assert r.status_code == 201
            responses.append(r.json())
        positions = [r["position"] for r in responses]
        assert positions == [0, 1, 2]

    def test_count_cap_rejected_with_400(
        self, test_client, setup_mailbox_and_account, isolated_db,
    ):
        # D-03: a draft holds at most 25 attachments. Seed 25 directly so the
        # 26th upload is rejected without buffering 25 real files.
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)
        with isolated_db.cursor() as cur:
            for i in range(25):
                _seed_draft_attachment(cur, account_id, draft_id, position=i, size=1)

        response = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("twenty-sixth.pdf", io.BytesIO(b"x"), "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "attachment_limit_exceeded"

    def test_cumulative_size_cap_rejected_with_400(
        self, test_client, setup_mailbox_and_account, isolated_db,
    ):
        # D-02: the cumulative size of a draft's attachments is capped at
        # 25 MB. Seed one row whose declared size is 25 MB (tiny blob) so a
        # small follow-up upload tips the total over the cap.
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)
        with isolated_db.cursor() as cur:
            _seed_draft_attachment(
                cur, account_id, draft_id, position=0, size=25 * 1024 * 1024,
            )

        response = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("small.pdf", io.BytesIO(b"x" * 1024), "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "attachment_message_size_exceeded"


# ── DELETE /attachments/{id} ───────────────────────────────────────


class TestRemoveDraftAttachment:

    def test_happy_path_returns_status_deleted_and_followup_404(
        self, test_client, setup_mailbox_and_account,
    ):
        # Common-mistakes §1: keep the destructive action and the
        # follow-up "is it actually gone?" assertion in the same test —
        # the delete + verify is a single logical operation, not two
        # endpoints.
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)

        upload = test_client.post(
            _attachments_url(mailbox_id, account_id, draft_id),
            files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")},
        )
        attachment_id = upload.json()["draft_attachment_id"]

        response = test_client.delete(
            _attachment_item_url(mailbox_id, account_id, draft_id, attachment_id),
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "deleted"}

        # Follow-up: a second DELETE on the same id must return 404 because
        # the row no longer exists. This catches the bug where ``delete``
        # accidentally returns success on a second call (idempotence is at
        # the response level only, not at the row level).
        followup = test_client.delete(
            _attachment_item_url(mailbox_id, account_id, draft_id, attachment_id),
        )
        assert followup.status_code == 404
        assert followup.json()["error"]["code"] == "draft_attachment_not_found"

    def test_unknown_attachment_returns_404(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, account_id = setup_mailbox_and_account(test_client)
        draft_id = _create_draft(test_client, mailbox_id, account_id)
        response = test_client.delete(
            _attachment_item_url(
                mailbox_id, account_id, draft_id,
                "00000000-0000-0000-0000-000000000000",
            ),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "draft_attachment_not_found"

    def test_unknown_account_returns_404(
        self, test_client, setup_mailbox_and_account,
    ):
        mailbox_id, _account_id = setup_mailbox_and_account(test_client)
        ghost_account = "00000000-0000-0000-0000-000000000000"
        response = test_client.delete(
            _attachment_item_url(
                mailbox_id, ghost_account, "draft-x",
                "00000000-0000-0000-0000-000000000000",
            ),
        )
        assert response.status_code == 404
