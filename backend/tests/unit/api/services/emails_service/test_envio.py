"""Tests espejo de ``emails_service.envio``: send_email."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, envio
from core.email import EmailManager
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient, build_metadata

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


def _patch_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for envio tests."""
    monkeypatch.setattr(
        envio, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        envio.account_store, "list_by_mailbox",
        lambda _mb: [_fake_account()],
    )
    monkeypatch.setattr(
        envio.account_store, "get",
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
        envio.account_store, "upsert_tokens",
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
                metadata=[build_metadata()],
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs,
            ))
        return manager

    monkeypatch.setattr(envio, "build_manager_for_accounts", _build)

    # Stub persistence helpers
    monkeypatch.setattr(envio, "persist_email_metadata_batch", lambda _aid, _meta, **_kw: len(_meta))


class TestSendEmail:

    def _make_payload(self, account_id=_ACCOUNT_ID):
        from api.schemas.email import EmailSendRequest
        return EmailSendRequest(
            account_id=account_id,
            subject="Hello",
            body="World",
            recipients=["dest@example.com"],
        )

    def test_happy_path(self, monkeypatch):
        _patch_common(monkeypatch)
        result = envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert result == {"status": "sent"}

    def test_account_not_found_raises_404(self, monkeypatch):
        _patch_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            envio.send_email(
                _MAILBOX_ID, self._make_payload("nonexistent"), _USER_ID,
            )

    def test_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_send_core_error_translated(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "send_exc": EmailExternalAPIError("SMTP reject"),
        })
        with pytest.raises(ExternalAPIError):
            envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_send_generic_exception_raises_external_api_error(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "send_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(ExternalAPIError):
            envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)

    def test_persists_refreshed_tokens(self, monkeypatch):
        _patch_common(monkeypatch)
        upsert_calls = []
        monkeypatch.setattr(
            envio.account_store, "upsert_tokens",
            lambda *args, **kwargs: upsert_calls.append(args),
        )

        def _build_refreshing(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_silent_return={"access_token": "new_tok", "refresh_token": "new_ref"},
                ))
            return manager

        monkeypatch.setattr(envio, "build_manager_for_accounts", _build_refreshing)
        envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert len(upsert_calls) >= 1

    def test_persists_sent_metadata(self, monkeypatch):
        """After send, metadata is persisted with the correct account_id."""
        _patch_common(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(
            envio, "persist_email_metadata_batch",
            lambda aid, meta, **_kw: (persist_calls.append((aid, meta)), len(meta))[1],
        )
        envio.send_email(_MAILBOX_ID, self._make_payload(), _USER_ID)
        assert len(persist_calls) == 1
        aid, meta_list = persist_calls[0]
        assert aid == _ACCOUNT_ID
        assert len(meta_list) == 1
        assert meta_list[0].box == "SENT"

    def test_html_body_sanitised_before_provider_send(self, monkeypatch):
        # The outbound sanitiser runs at the trust boundary before the body
        # reaches the provider's send_email (Gmail multipart / Outlook HTML).
        from api.schemas.email import EmailSendRequest
        _patch_common(monkeypatch)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                label = f"{acc.get('mailbox_id', '')}__{acc.get('account_id', '')}"
                client = FakeEmailClient(
                    label, auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(envio, "build_manager_for_accounts", _build)
        payload = EmailSendRequest(
            account_id=_ACCOUNT_ID,
            subject="Hello",
            body='<script>steal()</script><p>real <strong>body</strong></p>',
            recipients=["dest@example.com"],
        )
        envio.send_email(_MAILBOX_ID, payload, _USER_ID)
        # The fake records (subject, body, recipients) on ``sent_emails``.
        _subject, sent_body, _recipients = captured_clients[0].sent_emails[0]
        assert "<script>" not in sent_body
        assert "<p>real <strong>body</strong></p>" in sent_body
