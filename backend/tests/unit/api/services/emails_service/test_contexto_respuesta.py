"""Tests espejo de ``emails_service.contexto_respuesta``: get_reply_context."""

from __future__ import annotations

import pytest

from api.errors.exceptions import (
    AccountNotFound,
    EmailNotFound,
)
from api.services.emails_service import _comunes, contexto_respuesta
from core.email import EmailManager
from tests.shared.email_fakes import FakeEmailClient

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
)


def _patch_reply_context_common(
    monkeypatch,
    *,
    fake_client_kwargs=None,
    account_provider: str = "gmail",
    account_email: str | None = "me@me.com",
):
    """Common monkeypatches for ``get_reply_context`` tests.

    The reply context endpoint is read-only, so it shares the
    ``ensure_mailbox_access`` → account lookup → silent auth → manager
    call → translate cascade with the rest of the email service.
    """
    monkeypatch.setattr(
        contexto_respuesta, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )

    def _get(_mb, _aid):
        if _aid != _ACCOUNT_ID:
            return None
        return {
            "account_id": _ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": account_provider,
            "display_label": f"{account_provider}:{_ACCOUNT_ID}",
            "email_address": account_email,
        }
    monkeypatch.setattr(contexto_respuesta.account_store, "get", _get)
    monkeypatch.setattr(
        _comunes, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        contexto_respuesta.account_store, "upsert_tokens",
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

    monkeypatch.setattr(contexto_respuesta, "build_manager_for_accounts", _build)
    # Email metadata existence — default to True; tests that exercise
    # the missing-row path override.
    monkeypatch.setattr(
        contexto_respuesta.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )


def _build_reply_context_fake(
    *,
    from_email: str = "ana@x.com",
    from_name: str = "Ana",
    reply_to: list[str] | None = None,
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    subject: str = "Hello",
    message_id: str = "orig@x",
    references: str = "",
    thread_id: str = "thr-1",
    box: str = "ALL_MAIL",
):
    from datetime import datetime, timezone
    from core.email.email_client import ReplyContext
    return ReplyContext(
        provider_message_id="m1",
        thread_id=thread_id,
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to or [],
        to_recipients=to_recipients or ["someone@x.com"],
        cc_recipients=cc_recipients or [],
        subject=subject,
        body_html=None,
        body_text="body text",
        received_at=datetime(2026, 5, 23, 14, 32, tzinfo=timezone.utc),
        message_id=message_id,
        references=references,
        box=box,
    )


class TestGetReplyContext:
    """Covers the service-layer orchestration for GET /reply-context."""

    def test_reply_happy_path(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="ana@x.com",
                    to_recipients=["me@me.com", "carol@x.com"],
                    subject="Hello",
                    message_id="orig@x",
                ),
            },
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        # Reply: To = [from] (R-10 does not apply, no Reply-To).
        assert result.to_recipients == ["ana@x.com"]
        assert result.cc_recipients == []
        assert result.subject == "Re: Hello"
        # Headers wrapped with angle brackets.
        assert result.in_reply_to == "<orig@x>"
        assert "<orig@x>" in result.references
        assert result.thread_id == "thr-1"
        assert result.reply_to_message_id == "m1"
        assert result.reply_kind == "reply"
        assert result.original_from_email == "ana@x.com"

    def test_reply_body_is_html_with_blockquote(self, monkeypatch):
        # The reply body is now built by ``build_quoted_body_html``: an HTML
        # attribution line followed by the original quoted inside a
        # <blockquote> (was plain text with "> " before the rich-text feature).
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(),
            },
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        assert "<blockquote" in result.body
        assert "escribió:" in result.body
        # The degraded original rides inside the quote fragment.
        assert "body text" in result.body

    def test_reply_all_excludes_current_account_email(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="ana@x.com",
                    to_recipients=["me@me.com", "carol@x.com"],
                    cc_recipients=["dan@x.com"],
                ),
            },
            account_email="me@me.com",
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply_all", _USER_ID,
        )
        assert result.to_recipients == ["ana@x.com"]
        cc = result.cc_recipients
        assert "me@me.com" not in cc
        assert "carol@x.com" in cc
        assert "dan@x.com" in cc

    def test_reply_to_header_overrides_from_for_to(self, monkeypatch):
        # R-10: mailing list pattern — Reply-To wins over From.
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="bounce@list.com",
                    reply_to=["editor@list.com"],
                ),
            },
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        assert result.to_recipients == ["editor@list.com"]
        assert result.original_from_email == "bounce@list.com"

    def test_forward_returns_empty_recipients_and_fwd_prefix(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    subject="Hello",
                    to_recipients=["a@x"], cc_recipients=["b@x"],
                ),
            },
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "forward", _USER_ID,
        )
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.subject == "Fwd: Hello"
        assert result.reply_kind == "forward"
        # The quoted body uses the forward block header.
        assert "Mensaje reenviado" in result.body

    def test_email_not_found_when_metadata_missing(self, monkeypatch):
        # The local-existence pre-check short-circuits to 404 BEFORE the
        # provider call (mirrors the favourites toggle pattern).
        _patch_reply_context_common(monkeypatch)
        monkeypatch.setattr(
            contexto_respuesta.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )
        with pytest.raises(EmailNotFound):
            contexto_respuesta.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", "reply", _USER_ID,
            )

    def test_account_not_found(self, monkeypatch):
        _patch_reply_context_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            contexto_respuesta.get_reply_context(
                _MAILBOX_ID, "nonexistent", "m1", "reply", _USER_ID,
            )

    def test_invalid_action_raises_reply_context_error(self, monkeypatch):
        from api.errors.exceptions import EmailReplyContextError
        _patch_reply_context_common(monkeypatch)
        with pytest.raises(EmailReplyContextError):
            contexto_respuesta.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "weird-action", _USER_ID,
            )

    def test_ownership_checked_before_action_validation(self, monkeypatch):
        # api_guide "ownership check first": ensure_mailbox_access must run
        # before the action guard, so a foreign mailbox is rejected even
        # when the action is invalid (otherwise the guard would leak action
        # validity to a non-owner). Locks the ordering fix.
        from api.errors.exceptions import Forbidden
        _patch_reply_context_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Foreign mailbox in reply-context ownership test.")

        monkeypatch.setattr(contexto_respuesta, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            contexto_respuesta.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "weird-action", _USER_ID,
            )

    def test_provider_fetch_error_translated_to_reply_context_error(self, monkeypatch):
        from api.errors.exceptions import EmailReplyContextError
        from core.email.errors import EmailReplyContextFetchError
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_exc": EmailReplyContextFetchError(
                    "provider failed",
                    detail={"reason": "provider_fetch_failed"},
                ),
            },
        )
        with pytest.raises(EmailReplyContextError):
            contexto_respuesta.get_reply_context(
                _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
            )

    def test_self_reply_to_sent_box_uses_original_to(self, monkeypatch):
        _patch_reply_context_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_reply_context_return": _build_reply_context_fake(
                    from_email="me@me.com",
                    to_recipients=["client@x.com"],
                    box="SENT",
                ),
            },
            account_email="me@me.com",
        )
        result = contexto_respuesta.get_reply_context(
            _MAILBOX_ID, _ACCOUNT_ID, "m1", "reply", _USER_ID,
        )
        # Self-reply: To = original.to.
        assert result.to_recipients == ["client@x.com"]
