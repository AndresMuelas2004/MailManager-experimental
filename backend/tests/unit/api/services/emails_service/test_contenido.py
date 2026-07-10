"""Tests espejo de ``emails_service.contenido``: get_email_full_content y _persist_attachment_metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    EmailNotFound,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, contenido
from core.email import EmailContent, EmailManager
from core.email.errors import EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


def _patch_get_content_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for get_email_full_content tests."""
    monkeypatch.setattr(
        contenido, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        contenido.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        contenido.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    kwargs = fake_client_kwargs or {}

    def _build(accounts):
        manager = EmailManager()
        for acc in accounts:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(contenido, "build_manager_for_accounts", _build)

    # Default: metadata row exists — tests that need a missing metadata row
    # override this patch explicitly.
    monkeypatch.setattr(
        contenido.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )

    # Stub persist helper (best-effort, no-op by default)
    monkeypatch.setattr(contenido, "persist_email_content", lambda *_a, **_kw: None)
    # The unified cache-miss path (``_fetch_and_persist_email_content``) also
    # recomputes ``has_attachments`` and lists the attachment rows after the
    # provider read; stub both so the service test never touches the real DB.
    # The cache-HIT path touches the sliding TTL — stub that too (asserted in
    # the dedicated hit test).
    monkeypatch.setattr(contenido, "recompute_has_attachments", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        contenido.email_attachment_store, "list_by_message",
        lambda _aid, _mid: [],
    )
    monkeypatch.setattr(
        contenido, "touch_email_content_last_accessed", lambda *_a, **_kw: None,
    )


class TestGetEmailFullContent:

    def test_db_hit(self, monkeypatch):
        """When DB already has content, return it without calling core."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: {"html_body": "<p>cached</p>", "text_body": "cached"},
        )

        result = contenido.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.html_body == "<p>cached</p>"
        assert result.text_body == "cached"

    def test_db_hit_touches_sliding_ttl_and_skips_provider(self, monkeypatch):
        """A cache hit refreshes ``last_accessed_at`` (sliding TTL) and does NOT
        build a provider manager — the body is served straight from the DB."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: {"html_body": "<p>cached</p>", "text_body": "cached"},
        )

        touch_calls = []
        monkeypatch.setattr(
            contenido, "touch_email_content_last_accessed",
            lambda aid, mid: touch_calls.append((aid, mid)),
        )
        # If the hit branch reached the provider, this would explode.
        monkeypatch.setattr(
            contenido, "build_manager_for_accounts",
            lambda _accs: (_ for _ in ()).throw(
                AssertionError("provider must not be built on a cache hit"),
            ),
        )

        result = contenido.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.html_body == "<p>cached</p>"
        assert touch_calls == [(_ACCOUNT_ID, "m1")]

    def test_db_miss_fetches_from_provider(self, monkeypatch):
        """When DB returns None, fetch from provider (a SINGLE unified read) and
        persist. D4: ``fetch_content_with_attachments`` returns the body AND the
        attachment list in one round trip — verified via the fake's call list."""
        captured_clients = []

        def _capture_build(accounts):
            manager = EmailManager()
            for acc in accounts:
                label = f"{acc.get('mailbox_id', '')}__{acc.get('account_id', '')}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    email_content=EmailContent(
                        html_body="<p>from provider</p>", text_body="from provider",
                    ),
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(contenido, "build_manager_for_accounts", _capture_build)
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        persist_calls = []
        monkeypatch.setattr(
            contenido, "persist_email_content",
            lambda aid, mid, html, txt, **_kw: persist_calls.append((aid, mid, html, txt)),
        )

        result = contenido.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.text_body == "from provider"
        assert len(persist_calls) == 1
        # Verify persist was called with the account_id and message id
        assert persist_calls[0][0] == _ACCOUNT_ID
        assert persist_calls[0][1] == "m1"
        # Exactly ONE unified provider read happened (body + attachments fused).
        assert len(captured_clients) == 1
        assert captured_clients[0].fetch_content_with_attachments_calls == ["m1"]

    def test_sanitizes_html(self, monkeypatch):
        """HTML body from provider is sanitized before being persisted and returned."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "email_content": EmailContent(
                html_body="<p>safe</p><script>alert(1)</script>", text_body="safe",
            ),
        })
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        persist_calls = []
        monkeypatch.setattr(
            contenido, "persist_email_content",
            lambda aid, mid, html, txt, **_kw: persist_calls.append((aid, mid, html, txt)),
        )

        result = contenido.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert "<script>" not in (result.html_body or "")
        assert "<p>safe</p>" in (result.html_body or "")
        # Verify persisted html is also sanitized
        assert "<script>" not in (persist_calls[0][2] or "")

    def test_account_not_found(self, monkeypatch):
        """Non-existent account_id raises AccountNotFound."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        with pytest.raises(AccountNotFound):
            contenido.get_email_full_content(
                _MAILBOX_ID, "m1", "nonexistent", _USER_ID,
            )

    def test_core_error_translated(self, monkeypatch):
        """CoreError from manager.fetch_content_with_attachments is translated to
        EmailContentFetchError (mapped to ExternalAPIError). After D4 unification
        this is the SINGLE provider failure point in the cache-miss path; the
        fake raises it via the same ``fetch_content_exc`` injection."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "fetch_content_exc": EmailExternalAPIError("provider down"),
        })
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )

        with pytest.raises(ExternalAPIError):
            contenido.get_email_full_content(
                _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
            )

    def test_persist_failure_best_effort(self, monkeypatch):
        """If persist_email_content raises, the content is still returned."""
        _patch_get_content_common(monkeypatch, fake_client_kwargs={
            "email_content": EmailContent(
                html_body="<p>ok</p>", text_body="ok",
            ),
        })
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: None,
        )
        monkeypatch.setattr(
            contenido, "persist_email_content",
            MagicMock(side_effect=RuntimeError("db write failed")),
        )

        result = contenido.get_email_full_content(
            _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
        )
        assert result.text_body == "ok"
        assert result.html_body is not None

    def test_email_not_found_when_metadata_absent(self, monkeypatch):
        """Missing metadata row raises EmailNotFound before touching cache."""
        _patch_get_content_common(monkeypatch)
        monkeypatch.setattr(
            contenido.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )

        get_content_calls = []
        monkeypatch.setattr(
            contenido, "get_email_content",
            lambda _aid, _mid, **_kw: get_content_calls.append((_aid, _mid)) or None,
        )

        with pytest.raises(EmailNotFound):
            contenido.get_email_full_content(
                _MAILBOX_ID, "never-existed", _ACCOUNT_ID, _USER_ID,
            )
        # exists must short-circuit before cache read
        assert get_content_calls == []

    def test_metadata_exists_database_error_translated(self, monkeypatch):
        """DatabaseError during exists() is translated via translate_database_error."""
        from database.errors.exceptions import QueryError as DbQueryError
        from api.errors.exceptions import DatabaseQueryError

        _patch_get_content_common(monkeypatch)

        def _raise(_aid, _mid):
            raise DbQueryError("exists fail")

        monkeypatch.setattr(contenido.email_metadata_store, "exists", _raise)

        with pytest.raises(DatabaseQueryError):
            contenido.get_email_full_content(
                _MAILBOX_ID, "m1", _ACCOUNT_ID, _USER_ID,
            )


def _attachment_meta(
    *, filename, part_id="0.1", provider_attachment_id=None,
    mime_type="application/pdf", size=1024, content_id=None,
    is_inline=False, position=0,
):
    """Build an ``AttachmentMetadata`` instance for persistence tests."""
    from core.email import AttachmentMetadata
    return AttachmentMetadata(
        provider_message_id="m1",
        part_id=part_id,
        provider_attachment_id=provider_attachment_id,
        filename=filename,
        mime_type=mime_type,
        size=size,
        content_id=content_id,
        is_inline=is_inline,
        position=position,
    )


class TestPersistAttachmentMetadata:

    def _capture_rows(self, monkeypatch):
        captured: list[list[dict]] = []
        monkeypatch.setattr(
            contenido.email_attachment_store, "upsert_batch",
            lambda rows: captured.append(rows),
        )
        return captured

    def test_empty_list_does_not_call_store(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        contenido._persist_attachment_metadata(_ACCOUNT_ID, "m1", [])
        assert captured == []

    def test_clean_filename_passes_through(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="report.pdf")],
        )
        assert captured[0][0]["filename"] == "report.pdf"

    def test_path_traversal_is_neutralised(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="../../etc/passwd")],
        )
        persisted = captured[0][0]["filename"]
        assert ".." not in persisted
        assert "/" not in persisted

    def test_windows_reserved_name_is_prefixed(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="CON.pdf")],
        )
        assert captured[0][0]["filename"].startswith("_")

    def test_duplicate_names_within_message_are_deduped(self, monkeypatch):
        captured = self._capture_rows(monkeypatch)
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1",
            [
                _attachment_meta(filename="doc.pdf", part_id="0.1", position=0),
                _attachment_meta(filename="doc.pdf", part_id="0.2", position=1),
            ],
        )
        names = [row["filename"] for row in captured[0]]
        assert names == ["doc.pdf", "doc (1).pdf"]

    def test_mime_type_is_persisted_verbatim(self, monkeypatch):
        # mime_type is resolved upstream in the provider client (B-MIME);
        # _persist_attachment_metadata must not touch it.
        captured = self._capture_rows(monkeypatch)
        resolved_mime = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1",
            [_attachment_meta(filename="sheet.xlsx", mime_type=resolved_mime)],
        )
        assert captured[0][0]["mime_type"] == resolved_mime

    def test_upsert_database_error_is_swallowed(self, monkeypatch):
        # Persistence is best-effort (soft-fail) — a DatabaseError must not
        # propagate out of the helper.
        from database.errors.exceptions import QueryError as DbQueryError

        def _raise(_rows):
            raise DbQueryError("upsert failed")

        monkeypatch.setattr(
            contenido.email_attachment_store, "upsert_batch", _raise,
        )
        # No exception expected.
        contenido._persist_attachment_metadata(
            _ACCOUNT_ID, "m1", [_attachment_meta(filename="report.pdf")],
        )
