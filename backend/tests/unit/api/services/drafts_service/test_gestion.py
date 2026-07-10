"""Tests espejo de ``drafts_service.gestion``: create / update / delete / list de borradores."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    DatabaseQueryError,
    DraftCreationError,
    DraftDeleteError,
    DraftListError,
    DraftNotFound,
    DraftUpdateError,
    ExternalAPIError,
    Forbidden,
)
from api.schemas.draft import DraftCreate, DraftUpdate
from api.services.drafts_service import gestion
from core.email import EmailManager
from core.email.errors import (
    EmailAuthError,
    EmailExternalAPIError,
)
from database.errors import QueryError as DbQueryError
from tests.shared.email_fakes import FakeEmailClient

from ._helpers import (
    _ACCOUNT_ID,
    _MAILBOX_ID,
    _PROVIDER,
    _USER_ID,
    _fake_account,
    _patch_common,
    _persisted_row,
)


class TestCreateDraft:

    def _make_payload(self) -> DraftCreate:
        return DraftCreate(
            to_recipients=["to@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Hello draft",
            body="body",
        )

    def test_happy_path_returns_draft_out(self, monkeypatch):
        _patch_common(monkeypatch, gestion)
        result = gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
        )
        assert result.provider_draft_id == "fake_draft_1"
        assert result.account_id == _ACCOUNT_ID
        assert result.to_recipients == ["to@example.com"]
        assert result.subject == "Hello draft"
        assert result.body == "body"
        assert result.created_at == datetime(2024, 1, 1, 12, 0, 0)
        assert result.updated_at == datetime(2024, 1, 1, 12, 0, 0)

    def test_empty_draft_allowed(self, monkeypatch):
        _patch_common(monkeypatch, gestion)
        result = gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, DraftCreate(), _USER_ID,
        )
        assert result.subject == ""
        assert result.body == ""
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.bcc_recipients == []

    def test_account_not_found_raises(self, monkeypatch):
        _patch_common(monkeypatch, gestion)
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_common(monkeypatch, gestion)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(gestion, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        # EmailExternalAPIError maps to ExternalAPIError via _CORE_TO_API_MAP.
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "create_draft_exc": EmailExternalAPIError("Graph 400"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        # EmailManager wraps RuntimeError into EmailExternalAPIError,
        # which then translates to ExternalAPIError via _CORE_TO_API_MAP.
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "create_draft_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_db_persist_error_translated(self, monkeypatch):
        _patch_common(monkeypatch, gestion)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(gestion.draft_store, "create", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_common(monkeypatch, gestion)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(gestion.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_call_passes_payload_fields(self, monkeypatch):
        """Verify the FakeEmailClient receives the exact payload fields."""
        _patch_common(monkeypatch, gestion)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        payload = DraftCreate(
            to_recipients=["a@b.com", "c@d.com"],
            cc_recipients=["cc@e.com"],
            bcc_recipients=["bcc@f.com"],
            subject="My subject",
            body="plain body",
        )
        gestion.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        assert len(captured_clients) == 1
        assert len(captured_clients[0].create_draft_calls) == 1
        call = captured_clients[0].create_draft_calls[0]
        assert call == (
            ["a@b.com", "c@d.com"],
            ["cc@e.com"],
            ["bcc@f.com"],
            "My subject",
            "plain body",
        )

    def test_html_body_sanitised_before_provider_call(self, monkeypatch):
        # The HTML body is sanitised ONCE at the trust boundary; the cleaned
        # value (not the raw payload) is what reaches the provider client.
        _patch_common(monkeypatch, gestion)
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

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        payload = DraftCreate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<script>steal()</script><p>safe</p>',
        )
        gestion.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        provider_body = captured_clients[0].create_draft_calls[0][4]
        assert "<script>" not in provider_body
        assert "<p>safe</p>" in provider_body

    def test_html_body_sanitised_before_persist(self, monkeypatch):
        # The SAME sanitised value persists to the row — provider and DB
        # must never diverge.
        _patch_common(monkeypatch, gestion)
        captured_rows: list[dict] = []

        def _capture_create(row):
            captured_rows.append(row)
            return _persisted_row(
                provider_draft_id=row.get("provider_draft_id", "fake_draft_1"),
                body=row.get("body", ""),
            )

        monkeypatch.setattr(gestion.draft_store, "create", _capture_create)
        payload = DraftCreate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<script>steal()</script><p>safe</p>',
        )
        gestion.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        assert "<script>" not in captured_rows[0]["body"]
        assert "<p>safe</p>" in captured_rows[0]["body"]

    def test_persist_refreshed_tokens_happy_path(self, monkeypatch):
        # When authenticate_silent returns refreshed tokens, _persist_refreshed_tokens
        # must call account_store.upsert_tokens with the unwrapped values.
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            gestion.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
        )
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_persist_refreshed_tokens_db_error_raises_draft_creation_error(
        self, monkeypatch,
    ):
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_exception_raises_draft_creation_error(
        self, monkeypatch,
    ):
        _patch_common(monkeypatch, gestion, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftCreationError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_creation_error(self, monkeypatch):
        # Patch a helper called inside the outer try to raise a plain exception.
        # It must bubble up as DraftCreationError via the outer safety net.
        _patch_common(monkeypatch, gestion)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftCreationError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_inner_draft_store_exception_raises_draft_creation_error(self, monkeypatch):
        # RuntimeError from draft_store.create must be wrapped by the inner
        # except Exception into DraftCreationError (not caught only by the outer net).
        _patch_common(monkeypatch, gestion)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.draft_store, "create", _raise)
        with pytest.raises(DraftCreationError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_inner_account_store_exception_raises_draft_creation_error(
        self, monkeypatch,
    ):
        # RuntimeError from account_store.get must be wrapped into DraftCreationError.
        _patch_common(monkeypatch, gestion)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "get", _raise)
        with pytest.raises(DraftCreationError):
            gestion.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_none_fields_coalesced_to_defaults(self, monkeypatch):
        # When the persisted row has None for recipients/subject/body,
        # DraftOut must expose [] / "" via the `or []`/`or ""` coalescing.
        _patch_common(monkeypatch, gestion)
        monkeypatch.setattr(
            gestion.draft_store, "create",
            lambda row: {
                "provider_draft_id": "fake_draft_1",
                "account_id": _ACCOUNT_ID,
                "to_recipients": None,
                "cc_recipients": None,
                "bcc_recipients": None,
                "subject": None,
                "body": None,
                "created_at": datetime(2024, 1, 1, 12, 0, 0),
                "updated_at": datetime(2024, 1, 1, 12, 0, 0),
                # Phase 2.5: ``_draft_out_from_row`` short-circuits the
                # follow-up ``list_by_draft`` query when ``attachments`` is
                # already present, so the test never hits the DB.
                "attachments": [],
            },
        )
        result = gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
        )
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.bcc_recipients == []
        assert result.subject == ""
        assert result.body == ""


def _patch_list_common(monkeypatch):
    """Common monkeypatches for list_drafts tests."""
    monkeypatch.setattr(
        gestion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        gestion.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    # Default: list_by_account and list_by_mailbox return a single row.
    monkeypatch.setattr(
        gestion.draft_store, "list_by_account",
        lambda _aid: [_persisted_row(provider_draft_id="draft_a")],
    )
    monkeypatch.setattr(
        gestion.draft_store, "list_by_mailbox",
        lambda _mid: [
            _persisted_row(provider_draft_id="draft_a"),
            _persisted_row(provider_draft_id="draft_b"),
        ],
    )


class TestListDrafts:

    def test_list_single_account_happy_path(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "list_by_account",
            lambda _aid: [
                _persisted_row(provider_draft_id="d1", subject="first"),
                _persisted_row(provider_draft_id="d2", subject="second"),
            ],
        )
        result = gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(result) == 2
        assert result[0].provider_draft_id == "d1"
        assert result[0].subject == "first"
        assert result[0].account_id == _ACCOUNT_ID
        assert result[1].provider_draft_id == "d2"
        assert result[1].subject == "second"

    def test_list_unified_view_happy_path(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "list_by_mailbox",
            lambda _mid: [
                _persisted_row(provider_draft_id="d1"),
                _persisted_row(provider_draft_id="d2"),
                _persisted_row(provider_draft_id="d3"),
            ],
        )
        result = gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert len(result) == 3
        ids = [r.provider_draft_id for r in result]
        assert ids == ["d1", "d2", "d3"]

    def test_list_account_not_found_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_mailbox_access_denied_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(gestion, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_db_error_on_account_lookup_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(gestion.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_db_error_on_list_by_account_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("query failed")

        monkeypatch.setattr(gestion.draft_store, "list_by_account", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_db_error_on_list_by_mailbox_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("query failed")

        monkeypatch.setattr(gestion.draft_store, "list_by_mailbox", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_unexpected_error_on_list_by_account_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.draft_store, "list_by_account", _raise)
        with pytest.raises(DraftListError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_unexpected_error_on_list_by_mailbox_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.draft_store, "list_by_mailbox", _raise)
        with pytest.raises(DraftListError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_unexpected_error_on_account_lookup_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "get", _raise)
        with pytest.raises(DraftListError):
            gestion.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_empty_result_returns_empty_list(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "list_by_mailbox",
            lambda _mid: [],
        )
        result = gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result == []

    def test_list_none_fields_coalesced_to_defaults(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "list_by_mailbox",
            lambda _mid: [
                {
                    "provider_draft_id": "d1",
                    "account_id": _ACCOUNT_ID,
                    "to_recipients": None,
                    "cc_recipients": None,
                    "bcc_recipients": None,
                    "subject": None,
                    "body": None,
                    "created_at": datetime(2024, 1, 1, 12, 0, 0),
                    "updated_at": datetime(2024, 1, 1, 12, 0, 0),
                    # Phase 2.5: pre-aggregated by the listing query.
                    "attachments": [],
                },
            ],
        )
        result = gestion.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert len(result) == 1
        assert result[0].to_recipients == []
        assert result[0].cc_recipients == []
        assert result[0].bcc_recipients == []
        assert result[0].subject == ""
        assert result[0].body == ""


# =====================================================================
# update_draft
# =====================================================================


_PROVIDER_DRAFT_ID = "fake_draft_1"


def _patch_update_common(monkeypatch, *, fake_client_kwargs=None):
    """Same shape as _patch_common but wires draft_store.get + update.

    draft_store.get returns a pre-seeded row so the pre-check passes;
    draft_store.update echoes the incoming row with deterministic timestamps.
    """
    _patch_common(monkeypatch, gestion, fake_client_kwargs=fake_client_kwargs)

    monkeypatch.setattr(
        gestion.draft_store, "get",
        lambda _pdid, _aid: _persisted_row(provider_draft_id=_pdid),
    )
    monkeypatch.setattr(
        gestion.draft_store, "update",
        lambda row: _persisted_row(
            provider_draft_id=row.get("provider_draft_id", _PROVIDER_DRAFT_ID),
            to_recipients=row.get("to_recipients", []),
            cc_recipients=row.get("cc_recipients", []),
            bcc_recipients=row.get("bcc_recipients", []),
            subject=row.get("subject", ""),
            body=row.get("body", ""),
        ),
    )


class TestUpdateDraft:

    def _make_payload(self) -> DraftUpdate:
        return DraftUpdate(
            to_recipients=["updated@example.com"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Updated subject",
            body="updated",
        )

    def test_happy_path_returns_draft_out(self, monkeypatch):
        _patch_update_common(monkeypatch)
        result = gestion.update_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
            self._make_payload(), _USER_ID,
        )
        assert result.provider_draft_id == _PROVIDER_DRAFT_ID
        assert result.account_id == _ACCOUNT_ID
        assert result.to_recipients == ["updated@example.com"]
        assert result.subject == "Updated subject"
        assert result.body == "updated"

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(gestion, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "get",
            lambda _pdid, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(gestion.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_unexpected_exception_raises_draft_update_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "get", _raise)
        with pytest.raises(DraftUpdateError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_db_lookup_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("draft lookup failed")

        monkeypatch.setattr(gestion.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_db_lookup_unexpected_exception_raises_draft_update_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.draft_store, "get", _raise)
        with pytest.raises(DraftUpdateError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "update_draft_exc": EmailExternalAPIError("Graph 400"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        # EmailManager wraps RuntimeError into EmailExternalAPIError which
        # translates to ExternalAPIError via _CORE_TO_API_MAP.
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "update_draft_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_db_update_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(gestion.draft_store, "update", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_db_update_unexpected_exception_raises_draft_update_error(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.draft_store, "update", _raise)
        with pytest.raises(DraftUpdateError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_provider_call_passes_payload_fields(self, monkeypatch):
        """Verify the FakeEmailClient receives the exact payload fields."""
        _patch_update_common(monkeypatch)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        payload = DraftUpdate(
            to_recipients=["a@b.com", "c@d.com"],
            cc_recipients=["cc@e.com"],
            bcc_recipients=["bcc@f.com"],
            subject="New subject",
            body="new",
        )
        gestion.update_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID, payload, _USER_ID,
        )
        assert len(captured_clients) == 1
        assert len(captured_clients[0].update_draft_calls) == 1
        call = captured_clients[0].update_draft_calls[0]
        assert call == (
            _PROVIDER_DRAFT_ID,
            ["a@b.com", "c@d.com"],
            ["cc@e.com"],
            ["bcc@f.com"],
            "New subject",
            "new",
        )

    def test_html_body_sanitised_before_provider_replacement(self, monkeypatch):
        # update_draft sanitises the HTML body at entry; the cleaned value
        # reaches the provider replacement call (index 5 of the tuple).
        _patch_update_common(monkeypatch)
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

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        payload = DraftUpdate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<img src="x" onerror="hack()"><p>kept</p>',
        )
        gestion.update_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID, payload, _USER_ID,
        )
        provider_body = captured_clients[0].update_draft_calls[0][5]
        assert "onerror" not in provider_body
        assert "<img" not in provider_body
        assert "<p>kept</p>" in provider_body

    def test_persist_refreshed_tokens_happy_path(self, monkeypatch):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            gestion.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        gestion.update_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
            self._make_payload(), _USER_ID,
        )
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_persist_refreshed_tokens_db_error_raises_database_query_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_exception_raises_draft_update_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftUpdateError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_update_error(self, monkeypatch):
        # A RuntimeError raised inside the outer try (e.g. from load_wrapped_app_credentials)
        # must bubble up as DraftUpdateError via the outer safety net.
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftUpdateError):
            gestion.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_none_fields_coalesced_to_defaults(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "update",
            lambda row: {
                "provider_draft_id": _PROVIDER_DRAFT_ID,
                "account_id": _ACCOUNT_ID,
                "to_recipients": None,
                "cc_recipients": None,
                "bcc_recipients": None,
                "subject": None,
                "body": None,
                "created_at": datetime(2024, 1, 1, 12, 0, 0),
                "updated_at": datetime(2024, 1, 1, 12, 0, 0),
                # Phase 2.5: short-circuit the follow-up attachments fetch.
                "attachments": [],
            },
        )
        result = gestion.update_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
            self._make_payload(), _USER_ID,
        )
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.bcc_recipients == []
        assert result.subject == ""
        assert result.body == ""


# ------------------------------------------------------------------
# delete_draft
# ------------------------------------------------------------------

_DELETE_DRAFT_ID = "draft_to_delete"


def _patch_delete_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for delete_draft tests."""
    monkeypatch.setattr(
        gestion, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        gestion.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        gestion.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _DELETE_DRAFT_ID else None,
    )
    monkeypatch.setattr(
        gestion, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        gestion, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        gestion.account_store, "upsert_tokens",
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

    monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gestion.draft_store, "delete",
        lambda did, aid: deleted.append((aid, did)),
    )
    return deleted


class TestDeleteDraft:

    def test_happy_path_returns_status_deleted(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        result = gestion.delete_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
        )
        assert result == {"status": "deleted"}

    def test_happy_path_calls_provider_and_db(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        gestion.delete_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
        )
        assert len(captured_clients) == 1
        assert captured_clients[0].delete_draft_calls == [_DELETE_DRAFT_ID]
        assert deleted == [(_ACCOUNT_ID, _DELETE_DRAFT_ID)]

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        monkeypatch.setattr(
            gestion.draft_store, "get",
            lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(gestion, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "delete_draft_exc": EmailExternalAPIError("Graph 404"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        # Provider-First: DB must NOT be touched when provider fails.
        assert deleted == []

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "delete_draft_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        assert deleted == []

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        assert deleted == []

    def test_db_error_on_delete_translated(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(gestion.draft_store, "delete", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_db_error_on_draft_get_translated(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(gestion.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_refreshed_tokens_persisted(self, monkeypatch):
        _patch_delete_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            gestion.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        gestion.delete_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
        )
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_db_error_on_account_get_translated(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("account lookup failed")

        monkeypatch.setattr(gestion.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_db_error_raises_database_query_error(
        self, monkeypatch,
    ):
        _patch_delete_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_exception_raises_draft_delete_error(
        self, monkeypatch,
    ):
        _patch_delete_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftDeleteError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_delete_error(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(gestion, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftDeleteError):
            gestion.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )


# ==================================================================
# Reply / Forward extensions to create_draft + send_draft
# ==================================================================


class TestCreateDraftReplyMetadata:
    """The reply / forward request fields must reach both the provider
    client (via the manager) AND the local draft row insert."""

    def _make_reply_payload(self) -> DraftCreate:
        from uuid import uuid4
        return DraftCreate(
            to_recipients=["to@x"],
            cc_recipients=[],
            bcc_recipients=[],
            subject="Re: Hi",
            body="quoted body",
            reply_kind="reply",
            reply_to_message_id="orig-msg-1",
            reply_to_account_id=uuid4(),
            thread_id="thr-1",
            in_reply_to="<orig@x>",
            references_header="<older@x> <orig@x>",
        )

    def test_reply_fields_persist_into_draft_store(self, monkeypatch):
        # The row passed to ``draft_store.create`` must carry every
        # reply / forward column from the request payload.
        _patch_common(monkeypatch, gestion)
        captured: list[dict] = []

        def _create(row):
            captured.append(row)
            return _persisted_row(
                provider_draft_id=row.get("provider_draft_id", "fake_draft_1"),
                to_recipients=row.get("to_recipients", []),
                cc_recipients=row.get("cc_recipients", []),
                bcc_recipients=row.get("bcc_recipients", []),
                subject=row.get("subject", ""),
                body=row.get("body", ""),
            )
        monkeypatch.setattr(gestion.draft_store, "create", _create)

        payload = self._make_reply_payload()
        gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID,
        )
        assert len(captured) == 1
        row = captured[0]
        assert row["reply_kind"] == "reply"
        assert row["reply_to_message_id"] == "orig-msg-1"
        # UUID stringified for DB.
        assert row["reply_to_account_id"] == str(payload.reply_to_account_id)
        assert row["thread_id"] == "thr-1"
        assert row["in_reply_to"] == "<orig@x>"
        assert row["references_header"] == "<older@x> <orig@x>"

    def test_reply_kwargs_propagate_to_provider_client(self, monkeypatch):
        # The provider's create_draft receives every reply kwarg so that
        # Outlook can route to createReply/All/Forward and Gmail can
        # inject threading metadata.
        _patch_common(monkeypatch, gestion)
        captured_clients: list[FakeEmailClient] = []

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                client = FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                )
                captured_clients.append(client)
                manager.add_client(client)
            return manager

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)
        payload = self._make_reply_payload()
        gestion.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        assert len(captured_clients) == 1
        kw = captured_clients[0].create_draft_reply_kwargs[0]
        assert kw["thread_id"] == "thr-1"
        assert kw["in_reply_to"] == "<orig@x>"
        assert kw["references"] == "<older@x> <orig@x>"
        assert kw["reply_to_message_id"] == "orig-msg-1"
        assert kw["reply_kind"] == "reply"

    def test_outlook_forward_invokes_attachment_inheritance(self, monkeypatch):
        # Outlook ``createForward`` copies attachments server-side. The
        # service calls ``list_message_attachments`` against the newly
        # created draft to discover the inherited rows + persists them.
        _patch_common(monkeypatch, gestion)

        # Switch the test account to Outlook.
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: {
                "account_id": _ACCOUNT_ID,
                "mailbox_id": _MAILBOX_ID,
                "provider": "outlook",
                "display_label": "outlook:acc1",
            } if _aid == _ACCOUNT_ID else None,
        )

        # Capture the list_message_attachments call.
        from core.email.email_client import AttachmentMetadata
        inherited = AttachmentMetadata(
            provider_message_id="forwarded-draft-id",
            part_id=None,
            provider_attachment_id="att-inherited-1",
            filename="inherited.pdf",
            mime_type="application/pdf",
            size=128,
            content_id=None,
            is_inline=False,
            position=0,
        )

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    list_message_attachments_return=([inherited], {}),
                ))
            return manager

        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)

        inserted: list[dict] = []
        monkeypatch.setattr(
            gestion.draft_attachment_store, "insert",
            lambda row: inserted.append(row),
        )
        # Stamping step (best-effort): also stub list_by_draft + batch update.
        monkeypatch.setattr(
            gestion.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [
                {"draft_attachment_id": "uuid-1", "filename": "inherited.pdf"},
            ],
        )
        monkeypatch.setattr(
            gestion.draft_attachment_store, "batch_update_provider_attachment_ids",
            lambda _pairs: None,
        )

        from uuid import uuid4
        payload = DraftCreate(
            to_recipients=["to@x"],
            subject="Fwd: Hi",
            body="body",
            reply_kind="forward",
            reply_to_message_id="orig-fwd-1",
            reply_to_account_id=uuid4(),
        )
        gestion.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        # One row inserted into draft_attachments (the inherited one),
        # with the filename + size from the AttachmentMetadata.
        assert len(inserted) == 1
        assert inserted[0]["filename"] == "inherited.pdf"
        assert inserted[0]["size"] == 128
        assert inserted[0]["is_inline"] is False
        # The bytes are not downloaded — provider draft holds them.
        assert inserted[0]["blob"] is None

    def test_outlook_forward_inheritance_soft_fails(self, monkeypatch):
        # ``list_message_attachments`` failure during the inheritance
        # step is best-effort: the draft creation still succeeds.
        _patch_common(monkeypatch, gestion)
        monkeypatch.setattr(
            gestion.account_store, "get",
            lambda _mb, _aid: {
                "account_id": _ACCOUNT_ID,
                "mailbox_id": _MAILBOX_ID,
                "provider": "outlook",
                "display_label": "outlook:acc1",
            } if _aid == _ACCOUNT_ID else None,
        )
        # Configure the fake to raise during list_message_attachments.
        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    list_message_attachments_exc=RuntimeError("boom"),
                ))
            return manager
        monkeypatch.setattr(gestion, "build_manager_for_accounts", _build)

        # Should NOT raise — the response is still produced.
        from uuid import uuid4
        payload = DraftCreate(
            subject="Fwd: Hi", body="body",
            reply_kind="forward",
            reply_to_message_id="orig-1",
            reply_to_account_id=uuid4(),
        )
        result = gestion.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID,
        )
        # Draft created successfully despite the inheritance failure.
        assert result.provider_draft_id == "fake_draft_1"
