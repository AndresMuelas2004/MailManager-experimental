"""Tests espejo de ``emails_service.conversacion``: get_conversation y mapeo de error 502."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    ConversationFetchError,
    EmailNotFound,
    ExternalAPIError,
)
from api.services.emails_service import _comunes, conversacion
from core.email import EmailManager
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import FakeEmailClient, build_conversation_message

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


_CONVERSATION_BASE_ROW = {
    "provider_message_id": "m_base",
    "account_id": _ACCOUNT_ID,
    "mailbox_id": _MAILBOX_ID,
    "thread_id": "thr-1",
    "from_email": "sender@test.com",
    "from_name": "Sender",
    "subject": "Hello",
    "received_at": "2025-01-15T10:00:00+00:00",
    "is_read": False,
    "box": "ALL_MAIL",
}


def test_conversation_message_maps_is_favorite_through():
    # is_favorite must survive the ConversationMessage → EmailMetadata mapping:
    # the metadata upsert now persists it, so dropping it (the old behaviour)
    # would silently un-favourite the row when the viewer completes the mailbox.
    result = conversacion._conversation_message_to_metadata(
        build_conversation_message(is_favorite=True), _ACCOUNT_ID,
    )
    assert result.is_favorite is True
    assert result.account_id == _ACCOUNT_ID


def test_conversation_message_maps_provider_message_id_override():
    # The reconciled stable id (keyword-only) overrides the message's own id so
    # a remapped Outlook member persists under the stored id; ``None`` keeps the
    # message's own id (Gmail / genuinely-new Outlook messages).
    msg = build_conversation_message(provider_message_id="D")
    remapped = conversacion._conversation_message_to_metadata(
        msg, _ACCOUNT_ID, provider_message_id="A",
    )
    assert remapped.provider_message_id == "A"
    kept = conversacion._conversation_message_to_metadata(msg, _ACCOUNT_ID)
    assert kept.provider_message_id == "D"


# ── _physical_message_identity / _build_id_remap (pure functions) ────

_DT = datetime(2025, 1, 1, 10, 0)


def _stored_row(
    provider_message_id, *, received_at=_DT, from_email="sender@test.com", subject="Hello",
):
    """A ``list_metadata_by_thread`` row (identity columns only)."""
    return {
        "provider_message_id": provider_message_id,
        "received_at": received_at,
        "from_email": from_email,
        "subject": subject,
    }


def test_physical_message_identity_normalizes_email_and_strips():
    # from_email is stripped + lowercased and subject is stripped, so the same
    # physical message hashes to one identity regardless of case / whitespace.
    a = conversacion._physical_message_identity(_DT, "  Sender@Test.com ", "  Hello ")
    b = conversacion._physical_message_identity(_DT, "sender@test.com", "Hello")
    assert a == b
    # received_at is the discriminant within a thread: a different instant differs.
    other = conversacion._physical_message_identity(
        datetime(2025, 1, 2, 10, 0), "sender@test.com", "Hello",
    )
    assert other != b


def test_physical_message_identity_tolerates_none_fields():
    # from_email / subject can be NULL in the stored row; they coalesce to "".
    assert conversacion._physical_message_identity(_DT, None, None) == (_DT, "", "")


def test_build_id_remap_reconciles_outlook_id_to_stored_stable_id():
    # Outlook: the conversation endpoint returns id 'D' for a message stored under
    # the stable id 'A' (same physical triple) → remap D→A so the persist UPDATEs.
    existing = [_stored_row("A")]
    members = [build_conversation_message(
        provider_message_id="D", received_at=_DT, from_email="sender@test.com", subject="Hello",
    )]
    assert conversacion._build_id_remap(existing, members) == {"D": "A"}


def test_build_id_remap_leaves_member_whose_own_id_is_already_stored():
    # Gmail (stable ids across endpoints): the member id is already stored, so the
    # membership guard skips it — a strict no-op.
    existing = [_stored_row("G1")]
    members = [build_conversation_message(
        provider_message_id="G1", received_at=_DT, from_email="sender@test.com", subject="Hello",
    )]
    assert conversacion._build_id_remap(existing, members) == {}


def test_build_id_remap_leaves_genuinely_new_member_unmapped():
    # A member whose physical triple is not stored is genuinely new → no remap;
    # it will be inserted under its own id.
    existing = [_stored_row("A")]
    members = [build_conversation_message(
        provider_message_id="N", received_at=datetime(2025, 2, 2, 8, 0),
        from_email="other@test.com", subject="Different",
    )]
    assert conversacion._build_id_remap(existing, members) == {}


def test_build_id_remap_is_deterministic_with_duplicate_stored_rows():
    # Past opens left duplicate rows for one physical message. The rows arrive
    # ordered (received_at DESC, provider_message_id), so ``setdefault`` reuses the
    # SAME representative the grouped listing picks: min provider_message_id among
    # the max received_at.
    existing = [_stored_row("id-aaa"), _stored_row("id-bbb"), _stored_row("id-ccc")]
    members = [build_conversation_message(
        provider_message_id="D", received_at=_DT, from_email="sender@test.com", subject="Hello",
    )]
    assert conversacion._build_id_remap(existing, members) == {"D": "id-aaa"}


def test_build_id_remap_empty_existing_rows_returns_empty():
    members = [build_conversation_message(provider_message_id="D")]
    assert conversacion._build_id_remap([], members) == {}


def _patch_get_conversation_common(
    monkeypatch,
    *,
    base_row="default",
    fake_client_kwargs=None,
    persist_exc=None,
    persist_calls=None,
    thread_rows=None,
    thread_read_exc=None,
):
    """Common monkeypatches for ``get_conversation`` tests.

    Patches the base-message read (``get_metadata``), the thread-metadata read
    the lazy-sync now issues to reconcile ids (``load_thread_metadata``,
    default → ``[]`` so tests never hit a real DB — override with
    ``thread_rows`` / ``thread_read_exc``), and the lazy-sync persist helper
    (``persist_email_metadata_batch``, recording into ``persist_calls`` when
    given) — the dependency set of the conversation path, narrower than
    ``_patch_common``.
    """
    monkeypatch.setattr(
        conversacion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        conversacion.account_store, "get",
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
        conversacion.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    row = _CONVERSATION_BASE_ROW if base_row == "default" else base_row
    monkeypatch.setattr(
        conversacion.email_metadata_store, "get_metadata",
        lambda _aid, _mid: row,
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

    monkeypatch.setattr(conversacion, "build_manager_for_accounts", _build)

    # The lazy-sync reconciles member ids against the thread's stored rows
    # before persisting. Default to a no-op read ([]) so the reconciliation is a
    # no-op unless a test seeds ``thread_rows`` / forces ``thread_read_exc``.
    if thread_read_exc is not None:
        def _load_thread(_aid, _tid, **_kw):
            raise thread_read_exc
    else:
        _rows = list(thread_rows or [])

        def _load_thread(_aid, _tid, **_kw):
            return list(_rows)
    monkeypatch.setattr(conversacion, "load_thread_metadata", _load_thread)

    if persist_exc is not None:
        def _persist(_aid, _meta, **_kw):
            raise persist_exc
        monkeypatch.setattr(conversacion, "persist_email_metadata_batch", _persist)
    else:
        def _persist(_aid, _meta, **_kw):
            if persist_calls is not None:
                persist_calls.append((_aid, list(_meta)))
            return len(_meta)
        monkeypatch.setattr(conversacion, "persist_email_metadata_batch", _persist)


class TestGetConversation:

    def test_threadless_base_maps_singleton_without_provider_call(self, monkeypatch):
        # thread_id='' → single-message conversation mapped from the base row
        # already read; the provider path is never entered.
        base = dict(_CONVERSATION_BASE_ROW, thread_id="")
        _patch_get_conversation_common(monkeypatch, base_row=base)
        # A manager build would mean the provider branch was reached — make it
        # explode so the singleton short-circuit is proven.
        def _explode(_accounts):
            raise AssertionError("manager must not be built for a threadless base")
        monkeypatch.setattr(conversacion, "build_manager_for_accounts", _explode)

        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == ""
        assert len(result.messages) == 1
        # The singleton is mapped from the base row via row_to_email_metadata_out.
        assert result.messages[0].provider_message_id == "m_base"
        assert result.messages[0].mailbox_id == _MAILBOX_ID

    def test_happy_path_orders_ascending_and_returns_conversation_out(self, monkeypatch):
        # Provider returns members out of order; the response is sorted
        # oldest-first and mapped from the FRESH provider state.
        members = [
            build_conversation_message(
                provider_message_id="m_new", thread_id="thr-1",
                received_at=datetime(2025, 1, 2, 9, 0), box="SENT", is_favorite=True,
            ),
            build_conversation_message(
                provider_message_id="m_old", thread_id="thr-1",
                received_at=datetime(2025, 1, 1, 9, 0), box="ALL_MAIL",
            ),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
        )
        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == "thr-1"
        assert [m.provider_message_id for m in result.messages] == ["m_old", "m_new"]
        # Per-message state comes from the provider members; account/mailbox
        # are stamped from the resolved account; has_attachments is B.lazy.
        m_new = result.messages[1]
        assert m_new.box == "SENT"
        assert m_new.is_favorite is True
        assert m_new.account_id == _ACCOUNT_ID
        assert m_new.mailbox_id == _MAILBOX_ID
        assert all(m.has_attachments is False for m in result.messages)

    def test_lazy_sync_upserts_members_with_is_favorite(self, monkeypatch):
        persist_calls: list = []
        members = [
            build_conversation_message(provider_message_id="m_fav", is_favorite=True),
            build_conversation_message(provider_message_id="m_plain", is_favorite=False),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
        )
        conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        # The lazy-sync upserts every member with its provider-fresh is_favorite
        # (the shared upsert is now provider-authoritative for favourites — no
        # separate re-apply): m_fav → True, m_plain → False.
        persisted = {m.provider_message_id: m for call in persist_calls for m in call[1]}
        assert persisted["m_fav"].is_favorite is True
        assert persisted["m_plain"].is_favorite is False

    def test_persist_failure_is_best_effort(self, monkeypatch):
        # A lazy-sync persist failure must NOT abort the viewer response.
        members = [build_conversation_message(provider_message_id="m1")]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_exc=RuntimeError("db write failed"),
        )
        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        assert result.thread_id == "thr-1"
        assert [m.provider_message_id for m in result.messages] == ["m1"]

    def test_lazy_sync_reconciles_outlook_id_to_stored_stable_id(self, monkeypatch):
        # Outlook: fetch_conversation returns the message under a
        # non-deterministic id 'D'; the thread already stores it under the stable
        # id 'A' (same physical triple). The lazy-sync persists under 'A'
        # (UPDATE) instead of inserting a duplicate 'D' row per open.
        persist_calls: list = []
        members = [build_conversation_message(
            provider_message_id="D", thread_id="thr-1",
            received_at=datetime(2025, 1, 1, 10, 0),
            from_email="sender@test.com", subject="Hello",
        )]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
            thread_rows=[{
                "provider_message_id": "A",
                "received_at": datetime(2025, 1, 1, 10, 0),
                "from_email": "sender@test.com",
                "subject": "Hello",
            }],
        )
        result = conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        persisted = {m.provider_message_id: m for call in persist_calls for m in call[1]}
        assert "A" in persisted
        assert "D" not in persisted
        # The RESPONSE must carry the same reconciled id the persist used: the
        # frontend drives content / favourite / reply-context / box moves
        # through these ids, and 'D' has no row (404s / zero-row updates).
        assert [m.provider_message_id for m in result.messages] == ["A"]

    def test_response_reconciled_id_keeps_fresh_provider_state(self, monkeypatch):
        # The remap swaps ONLY the id: box / is_read / is_favorite must still
        # reflect the provider's fresh state from THIS open, not the stored row.
        members = [build_conversation_message(
            provider_message_id="D", thread_id="thr-1",
            received_at=datetime(2025, 1, 1, 10, 0),
            from_email="sender@test.com", subject="Hello",
            box="SPAM", is_read=True, is_favorite=True,
        )]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            thread_rows=[{
                "provider_message_id": "A",
                "received_at": datetime(2025, 1, 1, 10, 0),
                "from_email": "sender@test.com",
                "subject": "Hello",
            }],
        )
        result = conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        msg = result.messages[0]
        assert msg.provider_message_id == "A"
        assert msg.box == "SPAM"
        assert msg.is_read is True
        assert msg.is_favorite is True

    def test_lazy_sync_persists_under_own_ids_when_already_stored(self, monkeypatch):
        # Gmail: the member's own id is already stored (stable across endpoints),
        # so the remap is empty and it persists under its own id — unchanged.
        persist_calls: list = []
        members = [build_conversation_message(
            provider_message_id="G1", thread_id="thr-1",
            received_at=datetime(2025, 1, 1, 10, 0),
            from_email="sender@test.com", subject="Hello",
        )]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
            thread_rows=[{
                "provider_message_id": "G1",
                "received_at": datetime(2025, 1, 1, 10, 0),
                "from_email": "sender@test.com",
                "subject": "Hello",
            }],
        )
        conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        persisted = {m.provider_message_id: m for call in persist_calls for m in call[1]}
        assert "G1" in persisted

    def test_lazy_sync_read_failure_degrades_to_verbatim_persist(self, monkeypatch):
        # A thread-metadata read failure must degrade to persisting the members
        # verbatim (no worse than before) and NEVER abort the viewer response.
        persist_calls: list = []
        members = [build_conversation_message(
            provider_message_id="D", thread_id="thr-1",
        )]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
            thread_read_exc=RuntimeError("thread read failed"),
        )
        result = conversacion.get_conversation(
            _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
        )
        # The viewer still returns, mapped from the provider's fresh state.
        assert [m.provider_message_id for m in result.messages] == ["D"]
        # Reconciliation swallowed the read error → persisted verbatim under 'D'.
        persisted = {m.provider_message_id: m for call in persist_calls for m in call[1]}
        assert "D" in persisted

    def test_lazy_sync_collapses_two_members_remapped_to_the_same_stable_id(self, monkeypatch):
        # Rare physical-key collision: two fetched members share the SAME physical
        # triple (received_at / from_email / subject) and both remap to the stored
        # stable id 'A'. They MUST collapse to ONE persisted row (the batch upsert
        # rejects the same conflict key twice — "cannot affect row a second time"),
        # keeping the LAST member (members arrive oldest-first, so the newest wins).
        persist_calls: list = []
        members = [
            build_conversation_message(
                provider_message_id="D1", thread_id="thr-1",
                received_at=datetime(2025, 1, 1, 10, 0),
                from_email="sender@test.com", subject="Hello", is_favorite=False,
            ),
            build_conversation_message(
                provider_message_id="D2", thread_id="thr-1",
                received_at=datetime(2025, 1, 1, 10, 0),
                from_email="sender@test.com", subject="Hello", is_favorite=True,
            ),
        ]
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"fetch_conversation_return": members},
            persist_calls=persist_calls,
            thread_rows=[{
                "provider_message_id": "A",
                "received_at": datetime(2025, 1, 1, 10, 0),
                "from_email": "sender@test.com",
                "subject": "Hello",
            }],
        )
        result = conversacion.get_conversation(_MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID)
        persisted = [m for call in persist_calls for m in call[1]]
        under_a = [m for m in persisted if m.provider_message_id == "A"]
        # Collapsed to exactly one row under the stable id; neither raw id survives.
        assert len(under_a) == 1
        assert not any(m.provider_message_id in {"D1", "D2"} for m in persisted)
        # The LAST (newest) member won the collapse.
        assert under_a[0].is_favorite is True
        # Response↔persistence parity: the viewer shows the same single entry
        # (duplicate (account, id) keys would collide as React keys / action
        # targets), carrying the winning member's fresh state.
        assert [m.provider_message_id for m in result.messages] == ["A"]
        assert result.messages[0].is_favorite is True

    def test_email_not_found_when_base_row_missing(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch, base_row=None)
        with pytest.raises(EmailNotFound):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", _USER_ID,
            )

    def test_account_not_found(self, monkeypatch):
        _patch_get_conversation_common(monkeypatch)
        with pytest.raises(AccountNotFound):
            conversacion.get_conversation(
                _MAILBOX_ID, "nonexistent", "m_base", _USER_ID,
            )

    def test_provider_external_error_translated_to_external_api_error(self, monkeypatch):
        # A genuine provider failure (EmailExternalAPIError) surfaces as
        # ExternalAPIError (502) via translate_core_error — NOT the
        # ConversationFetchError fallback.
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={
                "fetch_conversation_exc": EmailExternalAPIError("thread fetch failed"),
            },
        )
        with pytest.raises(ExternalAPIError):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_silent_auth_failure_translated_to_account_not_connected(self, monkeypatch):
        # A silent-auth failure (expired/revoked token) collected during
        # authenticate_all_silent surfaces as AccountNotConnected (409) via
        # raise_on_silent_auth_errors. This is the FIRST-call auth guard, not a
        # phantom post-fetch re-check: fetch_conversation is single-account and
        # never populates _last_errors (finding #1), so the only reachable
        # silent-auth branch is this one, before the provider fetch.
        _patch_get_conversation_common(
            monkeypatch,
            fake_client_kwargs={"auth_silent_exc": EmailAuthError("expired")},
        )
        with pytest.raises(AccountNotConnected):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_get_metadata_database_error_translated(self, monkeypatch):
        from database.errors.exceptions import QueryError as DbQueryError
        from api.errors.exceptions import DatabaseQueryError
        _patch_get_conversation_common(monkeypatch)

        def _raise(_aid, _mid):
            raise DbQueryError("get_metadata fail")
        monkeypatch.setattr(
            conversacion.email_metadata_store, "get_metadata", _raise,
        )
        with pytest.raises(DatabaseQueryError):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )

    def test_ownership_checked_first(self, monkeypatch):
        from api.errors.exceptions import Forbidden
        _patch_get_conversation_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Foreign mailbox in conversation ownership test.")
        monkeypatch.setattr(conversacion, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            conversacion.get_conversation(
                _MAILBOX_ID, _ACCOUNT_ID, "m_base", _USER_ID,
            )


def test_conversation_fetch_error_maps_to_502():
    # Lock the _STATUS_MAP registration: ConversationFetchError → 502, same
    # family as EmailContentFetchError / EmailReplyContextError.
    from fastapi import status
    from api.errors.handlers import _STATUS_MAP
    assert _STATUS_MAP[ConversationFetchError] == status.HTTP_502_BAD_GATEWAY
