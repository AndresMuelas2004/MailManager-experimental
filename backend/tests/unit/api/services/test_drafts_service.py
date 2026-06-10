"""
Unit tests for drafts_service.

All external dependencies are monkeypatched so tests run without DB or provider APIs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    AccountNotFound,
    AttachmentSendFailed,
    DatabaseQueryError,
    DraftCreationError,
    DraftDeleteError,
    DraftListError,
    DraftNotFound,
    DraftSendError,
    DraftSyncError,
    DraftUpdateError,
    ExternalAPIError,
    Forbidden,
)
from api.schemas.draft import DraftCreate, DraftSendOut, DraftUpdate
from api.services import drafts_service
from core.email import DraftMetadata, EmailManager
from core.email.errors import (
    EmailAttachmentSendFailed,
    EmailAuthError,
    EmailExternalAPIError,
)
from database.errors import QueryError as DbQueryError
from tests.shared.email_fakes import FakeEmailClient


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_PROVIDER = "gmail"
_LABEL = f"{_MAILBOX_ID}__{_ACCOUNT_ID}"


def _fake_account(account_id=_ACCOUNT_ID, provider=_PROVIDER) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": _MAILBOX_ID,
        "provider": provider,
        "display_label": f"{provider}:{account_id}",
    }


def _persisted_row(
    *,
    provider_draft_id: str = "fake_draft_1",
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
    subject: str = "Hello draft",
    body: str = "body",
    attachments: list[dict] | None = None,
) -> dict:
    # Phase 2.5: list_drafts queries now bring ``attachments`` pre-aggregated
    # (json_agg subquery) so ``_draft_out_from_row`` can skip the per-row
    # follow-up fetch. Tests therefore include the field by default; legacy
    # single-draft endpoints that did NOT carry it still work via the
    # fallback branch in ``_draft_out_from_row``.
    return {
        "provider_draft_id": provider_draft_id,
        "account_id": _ACCOUNT_ID,
        "to_recipients": to_recipients if to_recipients is not None else ["to@example.com"],
        "cc_recipients": cc_recipients if cc_recipients is not None else [],
        "bcc_recipients": bcc_recipients if bcc_recipients is not None else [],
        "subject": subject,
        "body": body,
        "created_at": datetime(2024, 1, 1, 12, 0, 0),
        "updated_at": datetime(2024, 1, 1, 12, 0, 0),
        "attachments": attachments if attachments is not None else [],
    }


def _patch_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for drafts_service tests."""
    monkeypatch.setattr(
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "upsert_tokens",
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

    monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

    # Default draft_store.create returns a deterministic row
    monkeypatch.setattr(
        drafts_service.draft_store, "create",
        lambda row: _persisted_row(
            provider_draft_id=row.get("provider_draft_id", "fake_draft_1"),
            to_recipients=row.get("to_recipients", []),
            cc_recipients=row.get("cc_recipients", []),
            bcc_recipients=row.get("bcc_recipients", []),
            subject=row.get("subject", ""),
            body=row.get("body", ""),
        ),
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
        _patch_common(monkeypatch)
        result = drafts_service.create_draft(
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
        _patch_common(monkeypatch)
        result = drafts_service.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, DraftCreate(), _USER_ID,
        )
        assert result.subject == ""
        assert result.body == ""
        assert result.to_recipients == []
        assert result.cc_recipients == []
        assert result.bcc_recipients == []

    def test_account_not_found_raises(self, monkeypatch):
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        # EmailExternalAPIError maps to ExternalAPIError via _CORE_TO_API_MAP.
        _patch_common(monkeypatch, fake_client_kwargs={
            "create_draft_exc": EmailExternalAPIError("Graph 400"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        # EmailManager wraps RuntimeError into EmailExternalAPIError,
        # which then translates to ExternalAPIError via _CORE_TO_API_MAP.
        _patch_common(monkeypatch, fake_client_kwargs={
            "create_draft_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_db_persist_error_translated(self, monkeypatch):
        _patch_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(drafts_service.draft_store, "create", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_provider_call_passes_payload_fields(self, monkeypatch):
        """Verify the FakeEmailClient receives the exact payload fields."""
        _patch_common(monkeypatch)
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        payload = DraftCreate(
            to_recipients=["a@b.com", "c@d.com"],
            cc_recipients=["cc@e.com"],
            bcc_recipients=["bcc@f.com"],
            subject="My subject",
            body="plain body",
        )
        drafts_service.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        payload = DraftCreate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<script>steal()</script><p>safe</p>',
        )
        drafts_service.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        provider_body = captured_clients[0].create_draft_calls[0][4]
        assert "<script>" not in provider_body
        assert "<p>safe</p>" in provider_body

    def test_html_body_sanitised_before_persist(self, monkeypatch):
        # The SAME sanitised value persists to the row — provider and DB
        # must never diverge.
        _patch_common(monkeypatch)
        captured_rows: list[dict] = []

        def _capture_create(row):
            captured_rows.append(row)
            return _persisted_row(
                provider_draft_id=row.get("provider_draft_id", "fake_draft_1"),
                body=row.get("body", ""),
            )

        monkeypatch.setattr(drafts_service.draft_store, "create", _capture_create)
        payload = DraftCreate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<script>steal()</script><p>safe</p>',
        )
        drafts_service.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
        assert "<script>" not in captured_rows[0]["body"]
        assert "<p>safe</p>" in captured_rows[0]["body"]

    def test_persist_refreshed_tokens_happy_path(self, monkeypatch):
        # When authenticate_silent returns refreshed tokens, _persist_refreshed_tokens
        # must call account_store.upsert_tokens with the unwrapped values.
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            drafts_service.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        drafts_service.create_draft(
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
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_exception_raises_draft_creation_error(
        self, monkeypatch,
    ):
        _patch_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftCreationError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_creation_error(self, monkeypatch):
        # Patch a helper called inside the outer try to raise a plain exception.
        # It must bubble up as DraftCreationError via the outer safety net.
        _patch_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftCreationError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_inner_draft_store_exception_raises_draft_creation_error(self, monkeypatch):
        # RuntimeError from draft_store.create must be wrapped by the inner
        # except Exception into DraftCreationError (not caught only by the outer net).
        _patch_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "create", _raise)
        with pytest.raises(DraftCreationError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_inner_account_store_exception_raises_draft_creation_error(
        self, monkeypatch,
    ):
        # RuntimeError from account_store.get must be wrapped into DraftCreationError.
        _patch_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise)
        with pytest.raises(DraftCreationError):
            drafts_service.create_draft(
                _MAILBOX_ID, _ACCOUNT_ID, self._make_payload(), _USER_ID,
            )

    def test_none_fields_coalesced_to_defaults(self, monkeypatch):
        # When the persisted row has None for recipients/subject/body,
        # DraftOut must expose [] / "" via the `or []`/`or ""` coalescing.
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "create",
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
        result = drafts_service.create_draft(
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
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    # Default: list_by_account and list_by_mailbox return a single row.
    monkeypatch.setattr(
        drafts_service.draft_store, "list_by_account",
        lambda _aid: [_persisted_row(provider_draft_id="draft_a")],
    )
    monkeypatch.setattr(
        drafts_service.draft_store, "list_by_mailbox",
        lambda _mid: [
            _persisted_row(provider_draft_id="draft_a"),
            _persisted_row(provider_draft_id="draft_b"),
        ],
    )


class TestListDrafts:

    def test_list_single_account_happy_path(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "list_by_account",
            lambda _aid: [
                _persisted_row(provider_draft_id="d1", subject="first"),
                _persisted_row(provider_draft_id="d2", subject="second"),
            ],
        )
        result = drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(result) == 2
        assert result[0].provider_draft_id == "d1"
        assert result[0].subject == "first"
        assert result[0].account_id == _ACCOUNT_ID
        assert result[1].provider_draft_id == "d2"
        assert result[1].subject == "second"

    def test_list_unified_view_happy_path(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "list_by_mailbox",
            lambda _mid: [
                _persisted_row(provider_draft_id="d1"),
                _persisted_row(provider_draft_id="d2"),
                _persisted_row(provider_draft_id="d3"),
            ],
        )
        result = drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert len(result) == 3
        ids = [r.provider_draft_id for r in result]
        assert ids == ["d1", "d2", "d3"]

    def test_list_account_not_found_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_mailbox_access_denied_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_db_error_on_account_lookup_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_db_error_on_list_by_account_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("query failed")

        monkeypatch.setattr(drafts_service.draft_store, "list_by_account", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_db_error_on_list_by_mailbox_raises(self, monkeypatch):
        _patch_list_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("query failed")

        monkeypatch.setattr(drafts_service.draft_store, "list_by_mailbox", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_unexpected_error_on_list_by_account_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "list_by_account", _raise)
        with pytest.raises(DraftListError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_unexpected_error_on_list_by_mailbox_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "list_by_mailbox", _raise)
        with pytest.raises(DraftListError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_list_unexpected_error_on_account_lookup_raises_draft_list_error(
        self, monkeypatch,
    ):
        _patch_list_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise)
        with pytest.raises(DraftListError):
            drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_list_empty_result_returns_empty_list(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "list_by_mailbox",
            lambda _mid: [],
        )
        result = drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result == []

    def test_list_none_fields_coalesced_to_defaults(self, monkeypatch):
        _patch_list_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "list_by_mailbox",
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
        result = drafts_service.list_drafts(_MAILBOX_ID, _USER_ID, None)
        assert len(result) == 1
        assert result[0].to_recipients == []
        assert result[0].cc_recipients == []
        assert result[0].bcc_recipients == []
        assert result[0].subject == ""
        assert result[0].body == ""


# =====================================================================
# TestSyncDrafts — unit tests for drafts_service.sync_drafts
# =====================================================================

_DRAFT_TS = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _sample_draft(provider_draft_id: str = "d1", subject: str = "S") -> DraftMetadata:
    return DraftMetadata(
        provider_draft_id=provider_draft_id,
        to_recipients=["to@example.com"],
        cc_recipients=[],
        bcc_recipients=[],
        subject=subject,
        body="hi",
        created_at=_DRAFT_TS,
        updated_at=_DRAFT_TS,
    )


def _patch_sync_common(
    monkeypatch,
    *,
    fake_client_kwargs=None,
    accounts=None,
):
    """Common monkeypatches for sync_drafts tests.

    Builds an EmailManager with one FakeEmailClient per account. Captures
    replace_all_for_account invocations in a list returned at the end.
    """
    if accounts is None:
        accounts = [_fake_account()]

    monkeypatch.setattr(
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get",
        lambda _mb, _aid: next(
            (a for a in accounts if a["account_id"] == _aid), None,
        ),
    )
    monkeypatch.setattr(
        drafts_service.account_store, "list_by_mailbox",
        lambda _mb: list(accounts),
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "upsert_tokens",
        lambda *_a, **_kw: None,
    )

    kwargs = fake_client_kwargs or {}

    def _build(accounts_list):
        manager = EmailManager()
        for acc in accounts_list:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            default_return = [_sample_draft(provider_draft_id=f"d_{aid}")]
            call_kwargs = {
                "auth_return": {"access_token": "tok", "refresh_token": "ref"},
                "fetch_drafts_return": default_return,
                **kwargs,
            }
            manager.add_client(FakeEmailClient(label, **call_kwargs))
        return manager

    monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

    replace_calls: list[tuple[str, list[dict]]] = []

    def _replace(account_id, drafts_list):
        replace_calls.append((account_id, list(drafts_list)))
        return len(drafts_list)

    monkeypatch.setattr(
        drafts_service.draft_store, "replace_all_for_account", _replace,
    )
    return replace_calls


class TestSyncDrafts:

    def test_sync_single_account_happy_path(self, monkeypatch):
        calls = _patch_sync_common(monkeypatch)
        result = drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert isinstance(result.total_synced, int)
        assert result.total_synced == 1
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == _ACCOUNT_ID
        assert result.accounts[0].provider == _PROVIDER
        assert result.accounts[0].drafts_synced == 1
        assert len(calls) == 1
        assert calls[0][0] == _ACCOUNT_ID

    def test_sync_mailbox_happy_path(self, monkeypatch):
        accounts = [
            _fake_account(account_id="acc-gmail", provider="gmail"),
            _fake_account(account_id="acc-outlook", provider="outlook"),
        ]
        calls = _patch_sync_common(monkeypatch, accounts=accounts)
        result = drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result.total_synced == 2
        assert len(result.accounts) == 2
        account_ids = {a.account_id for a in result.accounts}
        assert account_ids == {"acc-gmail", "acc-outlook"}
        providers = {a.provider for a in result.accounts}
        assert providers == {"gmail", "outlook"}
        assert len(calls) == 2

    def test_sync_account_not_found_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_mailbox_access_denied_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_sync_db_error_on_account_lookup_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_db_error_on_list_by_mailbox_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("list failed")

        monkeypatch.setattr(drafts_service.account_store, "list_by_mailbox", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, None)

    def test_sync_provider_external_api_error_translated(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": EmailExternalAPIError("Provider down"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("Token expired"),
        })
        with pytest.raises(AccountNotConnected):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_db_error_on_replace_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("persist failed")

        monkeypatch.setattr(
            drafts_service.draft_store, "replace_all_for_account", _raise_db,
        )
        with pytest.raises(DatabaseQueryError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_unexpected_error_on_replace_raises_draft_sync_error(
        self, monkeypatch,
    ):
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            drafts_service.draft_store, "replace_all_for_account", _raise,
        )
        with pytest.raises(DraftSyncError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_empty_accounts_returns_zero(self, monkeypatch):
        _patch_sync_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "list_by_mailbox",
            lambda _mb: [],
        )
        result = drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, None)
        assert result.total_synced == 0
        assert result.accounts == []

    def test_sync_rows_include_all_draft_fields(self, monkeypatch):
        """The rows passed to replace_all_for_account carry every DraftMetadata field."""
        calls = _patch_sync_common(monkeypatch)
        drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(calls) == 1
        _, rows = calls[0]
        assert len(rows) == 1
        row = rows[0]
        assert row["provider_draft_id"] == f"d_{_ACCOUNT_ID}"
        assert row["to_recipients"] == ["to@example.com"]
        assert row["cc_recipients"] == []
        assert row["bcc_recipients"] == []
        assert row["subject"] == "S"
        assert row["body"] == "hi"
        assert row["created_at"] == _DRAFT_TS
        assert row["updated_at"] == _DRAFT_TS

    def test_sync_persist_refreshed_tokens_happy_path(self, monkeypatch):
        # When authenticate_silent returns refreshed tokens, _persist_refreshed_tokens
        # must call account_store.upsert_tokens with the unwrapped values.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            drafts_service.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)
        assert len(upsert_calls) == 1
        mb, acc, prov, payload = upsert_calls[0]
        assert mb == _MAILBOX_ID
        assert acc == _ACCOUNT_ID
        assert prov == _PROVIDER
        assert payload["access_token"] == "new-at"
        assert payload["refresh_token"] == "new-rt"

    def test_sync_persist_refreshed_tokens_db_error_raises(self, monkeypatch):
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_persist_refreshed_tokens_unexpected_raises_draft_sync_error(
        self, monkeypatch,
    ):
        # After Bloque 3 refactor, _persist_refreshed_tokens accepts a `fallback`
        # parameter and sync_drafts passes DraftSyncError — so a plain RuntimeError
        # from upsert_tokens must surface as DraftSyncError (not DraftCreationError).
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftSyncError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_fetch_all_drafts_core_error_translated(self, monkeypatch):
        # fetch_all_drafts captures the error in _last_errors; the downstream
        # raise_on_silent_auth_errors call translates it to ExternalAPIError.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": EmailExternalAPIError("Provider 502"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_fetch_all_drafts_generic_exception_raises_draft_sync_error(
        self, monkeypatch,
    ):
        # A non-CoreError captured in _last_errors is surfaced by
        # translate_core_error via the fallback (DraftSyncError for sync_drafts).
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": RuntimeError("unexpected"),
        })
        with pytest.raises(DraftSyncError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_provider_runtime_error_raises_draft_sync_error(self, monkeypatch):
        # Documents the asymmetric behavior vs. create_draft: a plain RuntimeError
        # from FakeEmailClient.fetch_drafts is captured in _last_errors by
        # EmailManager.fetch_all_drafts (not wrapped into EmailExternalAPIError
        # like send_email does) and surfaces as DraftSyncError via the fallback.
        _patch_sync_common(monkeypatch, fake_client_kwargs={
            "fetch_drafts_exc": RuntimeError("boom"),
        })
        with pytest.raises(DraftSyncError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)

    def test_sync_build_auth_context_error_raises_draft_sync_error(self, monkeypatch):
        # Validates Bloque 1.3 fix: _build_draft_auth_context + build_manager_for_accounts
        # must be inside the outer try block so plain exceptions from those helpers
        # are caught and re-raised as DraftSyncError.
        _patch_sync_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftSyncError):
            drafts_service.sync_drafts(_MAILBOX_ID, _USER_ID, _ACCOUNT_ID)


# =====================================================================
# update_draft
# =====================================================================


_PROVIDER_DRAFT_ID = "fake_draft_1"


def _patch_update_common(monkeypatch, *, fake_client_kwargs=None):
    """Same shape as _patch_common but wires draft_store.get + update.

    draft_store.get returns a pre-seeded row so the pre-check passes;
    draft_store.update echoes the incoming row with deterministic timestamps.
    """
    _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)

    monkeypatch.setattr(
        drafts_service.draft_store, "get",
        lambda _pdid, _aid: _persisted_row(provider_draft_id=_pdid),
    )
    monkeypatch.setattr(
        drafts_service.draft_store, "update",
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
        result = drafts_service.update_draft(
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

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "get",
            lambda _pdid, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_account_db_lookup_unexpected_exception_raises_draft_update_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise)
        with pytest.raises(DraftUpdateError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_db_lookup_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("draft lookup failed")

        monkeypatch.setattr(drafts_service.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_draft_db_lookup_unexpected_exception_raises_draft_update_error(
        self, monkeypatch,
    ):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "get", _raise)
        with pytest.raises(DraftUpdateError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "update_draft_exc": EmailExternalAPIError("Graph 400"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.update_draft(
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
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_update_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_db_update_error_translated(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(drafts_service.draft_store, "update", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_db_update_unexpected_exception_raises_draft_update_error(self, monkeypatch):
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "update", _raise)
        with pytest.raises(DraftUpdateError):
            drafts_service.update_draft(
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        payload = DraftUpdate(
            to_recipients=["a@b.com", "c@d.com"],
            cc_recipients=["cc@e.com"],
            bcc_recipients=["bcc@f.com"],
            subject="New subject",
            body="new",
        )
        drafts_service.update_draft(
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        payload = DraftUpdate(
            to_recipients=["a@b.com"],
            subject="s",
            body='<img src="x" onerror="hack()"><p>kept</p>',
        )
        drafts_service.update_draft(
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
            drafts_service.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        drafts_service.update_draft(
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

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.update_draft(
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

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftUpdateError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_update_error(self, monkeypatch):
        # A RuntimeError raised inside the outer try (e.g. from load_wrapped_app_credentials)
        # must bubble up as DraftUpdateError via the outer safety net.
        _patch_update_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftUpdateError):
            drafts_service.update_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _PROVIDER_DRAFT_ID,
                self._make_payload(), _USER_ID,
            )

    def test_none_fields_coalesced_to_defaults(self, monkeypatch):
        _patch_update_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "update",
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
        result = drafts_service.update_draft(
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
        drafts_service, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "get",
        lambda _mb, _aid: _fake_account() if _aid == _ACCOUNT_ID else None,
    )
    monkeypatch.setattr(
        drafts_service.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _DELETE_DRAFT_ID else None,
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        drafts_service, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(
        drafts_service.account_store, "upsert_tokens",
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

    monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        drafts_service.draft_store, "delete",
        lambda did, aid: deleted.append((aid, did)),
    )
    return deleted


class TestDeleteDraft:

    def test_happy_path_returns_status_deleted(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        result = drafts_service.delete_draft(
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        drafts_service.delete_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
        )
        assert len(captured_clients) == 1
        assert captured_clients[0].delete_draft_calls == [_DELETE_DRAFT_ID]
        assert deleted == [(_ACCOUNT_ID, _DELETE_DRAFT_ID)]

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "get",
            lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_account_not_found_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise Forbidden("You do not have access to this mailbox.")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _raise)
        with pytest.raises(Forbidden):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "delete_draft_exc": EmailExternalAPIError("Graph 404"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        # Provider-First: DB must NOT be touched when provider fails.
        assert deleted == []

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "delete_draft_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        assert deleted == []

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        deleted = _patch_delete_common(monkeypatch, fake_client_kwargs={
            "auth_silent_exc": EmailAuthError("expired"),
        })
        with pytest.raises(AccountNotConnected):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )
        assert deleted == []

    def test_db_error_on_delete_translated(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("db down")

        monkeypatch.setattr(drafts_service.draft_store, "delete", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_db_error_on_draft_get_translated(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("lookup failed")

        monkeypatch.setattr(drafts_service.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.delete_draft(
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
            drafts_service.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        drafts_service.delete_draft(
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

        monkeypatch.setattr(drafts_service.account_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.delete_draft(
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

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.delete_draft(
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

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftDeleteError):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_delete_error(self, monkeypatch):
        _patch_delete_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftDeleteError):
            drafts_service.delete_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _DELETE_DRAFT_ID, _USER_ID,
            )


# ===================================================================
# send_draft
# ===================================================================

_SEND_DRAFT_ID = "draft_abc"


def _patch_send_common(monkeypatch, *, fake_client_kwargs=None):
    """Apply common monkeypatches for send_draft tests."""
    _patch_common(monkeypatch, fake_client_kwargs=fake_client_kwargs)

    # Pre-check: draft exists
    monkeypatch.setattr(
        drafts_service.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _SEND_DRAFT_ID else None,
    )
    # Best-effort delete (no-op)
    monkeypatch.setattr(drafts_service.draft_store, "delete", lambda _did, _aid: None)
    # Best-effort metadata persist (no-op)
    monkeypatch.setattr(
        drafts_service, "persist_email_metadata_batch",
        lambda _aid, _metas, **_kw: len(_metas),
    )
    # Draft attachments — the unified send path collects them from the
    # store before delegating to the manager. Tests that don't care about
    # attachments default to "no rows" so the path stays focused on the
    # provider behaviour they exercise. Tests that DO care override these
    # patches inline.
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "list_by_draft",
        lambda _aid, _did: [],
    )
    # The send path uses ``list_by_draft_with_blob`` (single round trip).
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "list_by_draft_with_blob",
        lambda _aid, _did: [],
    )
    monkeypatch.setattr(
        drafts_service.draft_attachment_store, "update_provider_attachment_id",
        lambda _id, _pid: None,
    )


class TestSendDraft:

    def test_happy_path_returns_draft_send_out(self, monkeypatch):
        _patch_send_common(monkeypatch)
        result = drafts_service.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert isinstance(result, DraftSendOut)
        assert result.status == "sent"
        assert result.provider_message_id == f"sent_{_SEND_DRAFT_ID}"
        assert result.provider == _PROVIDER

    def test_account_not_found_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get", lambda _mb, _aid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_not_found_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.draft_store, "get", lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_mailbox_access_denied_raises(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _deny(_mb, _uid):
            raise Forbidden("Access denied.")

        monkeypatch.setattr(drafts_service, "ensure_mailbox_access", _deny)
        with pytest.raises(Forbidden):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_provider_external_api_error_translated(self, monkeypatch):
        # The send path goes through ``send_draft_with_attachments`` even
        # for drafts without attachments — it is the unified entry point
        # for the new flow. Inject the failure on that method's kwarg.
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "send_draft_with_attachments_exc": EmailExternalAPIError(
                    "Provider boom",
                ),
            },
        )
        with pytest.raises(ExternalAPIError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_attachment_send_failure_persists_partial_results_then_raises(self, monkeypatch):
        # D-27 partial-success resume: Outlook's non-atomic send raises
        # EmailAttachmentSendFailed carrying the already-uploaded parts in
        # ``detail['succeeded']``. The service must persist their
        # provider_attachment_ids (so a retry skips them) BEFORE re-raising
        # the translated AttachmentSendFailed (502). This branch is distinct
        # from the EmailExternalAPIError / RuntimeError branches, which never
        # touch ``_persist_partial_upload_results``.
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "send_draft_with_attachments_exc": EmailAttachmentSendFailed(
                    "Outlook attachment upload failed mid-flight.",
                    detail={
                        "succeeded": [
                            {"draft_attachment_id": "da-1", "provider_attachment_id": "pa-1"},
                            {"draft_attachment_id": "da-2", "provider_attachment_id": "pa-2"},
                        ],
                        "failed_attachments": [
                            {"draft_attachment_id": "da-3", "filename": "big.pdf", "reason": "upload_failed"},
                        ],
                    },
                ),
            },
        )
        persisted_pairs: list[tuple[str, str]] = []
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "batch_update_provider_attachment_ids",
            lambda pairs: persisted_pairs.extend(pairs),
        )
        with pytest.raises(AttachmentSendFailed):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )
        # Only the two succeeded parts are stamped; the failed one stays NULL.
        assert persisted_pairs == [("da-1", "pa-1"), ("da-2", "pa-2")]

    def test_silent_auth_error_raises_account_not_connected(self, monkeypatch):
        _patch_send_common(
            monkeypatch,
            fake_client_kwargs={
                "auth_silent_exc": EmailAuthError("Token expired"),
            },
        )
        with pytest.raises(AccountNotConnected):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_db_draft_delete_failure_swallowed(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _explode(_did, _aid):
            raise DbQueryError("DB delete failed")

        monkeypatch.setattr(drafts_service.draft_store, "delete", _explode)
        result = drafts_service.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert result.status == "sent"

    def test_metadata_persist_failure_swallowed(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _explode(_aid, _metas, **_kw):
            raise RuntimeError("persist failed")

        monkeypatch.setattr(
            drafts_service, "persist_email_metadata_batch", _explode,
        )
        result = drafts_service.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert result.status == "sent"

    def test_account_db_lookup_error_translated(self, monkeypatch):
        _patch_send_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
            lambda _mb, _aid: (_ for _ in ()).throw(DbQueryError("DB fail")),
        )
        with pytest.raises(DatabaseQueryError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_account_db_lookup_unexpected_exception_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "get", _raise)
        with pytest.raises(DraftSendError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_db_lookup_error_translated(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _raise_db(*_a, **_kw):
            raise DbQueryError("draft lookup failed")

        monkeypatch.setattr(drafts_service.draft_store, "get", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_draft_db_lookup_unexpected_exception_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.draft_store, "get", _raise)
        with pytest.raises(DraftSendError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_provider_generic_exception_raises_external_api_error(self, monkeypatch):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "send_draft_with_attachments_exc": RuntimeError("boom"),
        })
        with pytest.raises(ExternalAPIError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_happy_path(self, monkeypatch):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expiry": "2030-01-01T00:00:00Z",
            },
        })
        upsert_calls: list[tuple] = []
        monkeypatch.setattr(
            drafts_service.account_store, "upsert_tokens",
            lambda mb, acc, prov, payload: upsert_calls.append((mb, acc, prov, payload)),
        )
        drafts_service.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
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
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise_db(*_a, **_kw):
            raise DbQueryError("tokens table down")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise_db)
        with pytest.raises(DatabaseQueryError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_persist_refreshed_tokens_unexpected_raises_draft_send_error(
        self, monkeypatch,
    ):
        _patch_send_common(monkeypatch, fake_client_kwargs={
            "auth_silent_return": {"access_token": "new-at", "refresh_token": "new-rt"},
        })

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service.account_store, "upsert_tokens", _raise)
        with pytest.raises(DraftSendError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
            )

    def test_outer_exception_safety_net_raises_draft_send_error(self, monkeypatch):
        _patch_send_common(monkeypatch)

        def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(drafts_service, "load_wrapped_app_credentials", _raise)
        with pytest.raises(DraftSendError):
            drafts_service.send_draft(
                _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
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
        _patch_common(monkeypatch)
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
        monkeypatch.setattr(drafts_service.draft_store, "create", _create)

        payload = self._make_reply_payload()
        drafts_service.create_draft(
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
        _patch_common(monkeypatch)
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)
        payload = self._make_reply_payload()
        drafts_service.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
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
        _patch_common(monkeypatch)

        # Switch the test account to Outlook.
        monkeypatch.setattr(
            drafts_service.account_store, "get",
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

        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

        inserted: list[dict] = []
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "insert",
            lambda row: inserted.append(row),
        )
        # Stamping step (best-effort): also stub list_by_draft + batch update.
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [
                {"draft_attachment_id": "uuid-1", "filename": "inherited.pdf"},
            ],
        )
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "batch_update_provider_attachment_ids",
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
        drafts_service.create_draft(_MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID)
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
        _patch_common(monkeypatch)
        monkeypatch.setattr(
            drafts_service.account_store, "get",
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
        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

        # Should NOT raise — the response is still produced.
        from uuid import uuid4
        payload = DraftCreate(
            subject="Fwd: Hi", body="body",
            reply_kind="forward",
            reply_to_message_id="orig-1",
            reply_to_account_id=uuid4(),
        )
        result = drafts_service.create_draft(
            _MAILBOX_ID, _ACCOUNT_ID, payload, _USER_ID,
        )
        # Draft created successfully despite the inheritance failure.
        assert result.provider_draft_id == "fake_draft_1"


class TestSendDraftPropagatesReplyMetadata:
    """``send_draft`` reads the reply metadata from the local row and
    passes ``in_reply_to`` / ``references`` / ``thread_id`` to
    ``send_draft_with_attachments`` — not from the request body."""

    def test_send_propagates_reply_kwargs_from_local_row(self, monkeypatch):
        _patch_send_common(monkeypatch)
        # Override the draft row to carry reply metadata.
        row = _persisted_row(provider_draft_id=_SEND_DRAFT_ID)
        row["reply_kind"] = "reply"
        row["reply_to_message_id"] = "orig-1"
        row["thread_id"] = "thr-row"
        row["in_reply_to"] = "<orig@x>"
        row["references_header"] = "<older@x> <orig@x>"
        monkeypatch.setattr(
            drafts_service.draft_store, "get",
            lambda _did, _aid: row if _did == _SEND_DRAFT_ID else None,
        )
        # Capture clients to inspect send_draft_with_attachments kwargs.
        captured: list[FakeEmailClient] = []

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
                captured.append(client)
                manager.add_client(client)
            return manager
        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

        drafts_service.send_draft(
            _MAILBOX_ID, _ACCOUNT_ID, _SEND_DRAFT_ID, _USER_ID,
        )
        assert len(captured) == 1
        kw = captured[0].send_draft_with_attachments_reply_kwargs[0]
        # Threading flows from the row, NOT from the request body.
        assert kw["in_reply_to"] == "<orig@x>"
        assert kw["references"] == "<older@x> <orig@x>"
        assert kw["thread_id"] == "thr-row"


# ==================================================================
# copy_attachments_from_email
# ==================================================================


_COPY_DRAFT_ID = "draft_copy"
_SOURCE_ACCOUNT_ID = "00000000-0000-4000-a000-000000000abc"
_SOURCE_MESSAGE_ID = "src-msg-1"
_SOURCE_ATTACHMENT_ID = "11111111-1111-4000-a000-aaaaaaaaaaaa"


def _patch_copy_attachments_common(monkeypatch, *, draft_provider: str = "gmail"):
    """Patch helpers needed by copy_attachments_from_email tests."""
    _patch_common(monkeypatch)

    # Draft account.
    def _get(_mb, _aid):
        if _aid != _ACCOUNT_ID:
            return None
        return {
            "account_id": _ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": draft_provider,
            "display_label": f"{draft_provider}:{_ACCOUNT_ID}",
            "email_address": "me@me.com",
        }
    monkeypatch.setattr(drafts_service.account_store, "get", _get)

    # Source account ownership (D-22 single-JOIN).
    monkeypatch.setattr(
        drafts_service.account_store, "get_by_id_for_user",
        lambda _aid, _uid: {
            "account_id": _SOURCE_ACCOUNT_ID,
            "mailbox_id": _MAILBOX_ID,
            "provider": "gmail",
            "display_label": "gmail:src",
            "email_address": "src@me.com",
        } if _aid == _SOURCE_ACCOUNT_ID else None,
    )

    # Draft exists.
    monkeypatch.setattr(
        drafts_service.draft_store, "get",
        lambda _did, _aid: _persisted_row(provider_draft_id=_did) if _did == _COPY_DRAFT_ID else None,
    )

    # Source email metadata exists.
    monkeypatch.setattr(
        drafts_service.email_metadata_store, "exists",
        lambda _aid, _mid: True,
    )

    # Loader for the final response (returns the current draft attachments).
    monkeypatch.setattr(
        drafts_service, "_load_draft_attachments_metadata_out",
        lambda _aid, _did: [],
    )


class TestCopyAttachmentsFromEmail:
    """Covers R-06 / R-12 — server-side copy of Forward attachments."""

    def _seed_one_source_attachment(
        self, monkeypatch, *, blob: bytes | None = b"BLOB-BYTES",
        unavailable_at=None,
    ):
        # Source email_attachments rows.
        rows = [{
            "attachment_id": _SOURCE_ATTACHMENT_ID,
            "filename": "src.pdf",
            "mime_type": "application/pdf",
            "size": len(blob) if blob else 1000,
            "content_id": None,
            "is_inline": False,
            "position": 0,
            "part_id": "1",
            "provider_attachment_id": None,
            "unavailable_at": unavailable_at,
        }]
        monkeypatch.setattr(
            drafts_service.email_attachment_store, "list_by_message",
            lambda _aid, _mid: rows,
        )
        monkeypatch.setattr(
            drafts_service.email_attachment_store, "get_blob",
            lambda _aid: blob,
        )
        # No prior copies — idempotency check returns empty.
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: set(),
        )
        # No pre-existing chips → counts start at zero.
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [],
        )
        inserts: list[dict] = []
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "insert",
            lambda row: inserts.append(row) or row,
        )
        return inserts

    def test_outlook_draft_returns_noop(self, monkeypatch):
        # The Outlook code path short-circuits before reading source
        # attachments — createForward already inherited them.
        _patch_copy_attachments_common(monkeypatch, draft_provider="outlook")
        monkeypatch.setattr(
            drafts_service.email_attachment_store, "list_by_message",
            lambda *_a, **_kw: pytest.fail("list_by_message must NOT be called for Outlook"),
        )
        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert result.skipped == []

    def test_gmail_cached_blob_inserts_draft_attachment(self, monkeypatch):
        # Happy path: source attachment row + cached blob → single insert.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=b"BLOB-BYTES")
        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 1
        assert result.skipped == []
        assert len(inserts) == 1
        # R-12 source-tracking columns set on the new row.
        assert inserts[0]["source_account_id"] == _SOURCE_ACCOUNT_ID
        assert inserts[0]["source_attachment_id"] == _SOURCE_ATTACHMENT_ID
        # Blob copied from the cache.
        assert inserts[0]["blob"] == b"BLOB-BYTES"

    def test_gmail_missing_blob_falls_back_to_provider_download(self, monkeypatch):
        # Cache miss path: fetch_attachment_binary is invoked, then
        # the bytes are persisted (cache-aside) and the draft row inserted.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=None)
        # Track insert_blob calls for cache-aside.
        blob_inserts: list[tuple] = []
        monkeypatch.setattr(
            drafts_service.email_attachment_store, "insert_blob",
            lambda aid, b: blob_inserts.append((aid, b)),
        )
        # Reconfigure the source manager fake to return a binary.
        from core.email.email_client import AttachmentBinary

        def _build(accounts):
            manager = EmailManager()
            for acc in accounts:
                mid = str(acc.get("mailbox_id", ""))
                aid = str(acc.get("account_id", ""))
                label = f"{mid}__{aid}"
                manager.add_client(FakeEmailClient(
                    label,
                    auth_return={"access_token": "tok", "refresh_token": "ref"},
                    fetch_attachment_binary_return=AttachmentBinary(
                        mime_type="application/pdf",
                        filename="src.pdf",
                        data=b"DOWNLOADED-BYTES",
                        size=16,
                    ),
                ))
            return manager
        monkeypatch.setattr(drafts_service, "build_manager_for_accounts", _build)

        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 1
        assert len(inserts) == 1
        # Blob was persisted into email_attachment_blobs (cache-aside).
        assert blob_inserts == [(_SOURCE_ATTACHMENT_ID, b"DOWNLOADED-BYTES")]
        # The draft attachment carries the downloaded bytes.
        assert inserts[0]["blob"] == b"DOWNLOADED-BYTES"

    def test_unavailable_at_source_skipped_with_reason(self, monkeypatch):
        # An attachment marked unavailable (D-17) is skipped without
        # touching the provider.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(
            monkeypatch, blob=b"DOES-NOT-MATTER", unavailable_at="2026-01-01T00:00:00Z",
        )
        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "unavailable_at_source"
        # Nothing inserted.
        assert inserts == []

    def test_idempotency_skips_already_copied_rows(self, monkeypatch):
        # R-12: a retry must NOT duplicate rows. ``list_existing_source_attachment_ids``
        # returns the ids we already copied; those are skipped.
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        inserts = self._seed_one_source_attachment(monkeypatch, blob=b"X")
        # Pretend the attachment was already copied.
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: {_SOURCE_ATTACHMENT_ID},
        )
        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        assert result.copied_count == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "already_copied"
        assert inserts == []

    def test_source_email_not_found_returns_404(self, monkeypatch):
        from api.errors.exceptions import EmailNotFound
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        monkeypatch.setattr(
            drafts_service.email_metadata_store, "exists",
            lambda _aid, _mid: False,
        )
        with pytest.raises(EmailNotFound):
            drafts_service.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
                _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_source_account_unknown_returns_account_not_found(self, monkeypatch):
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        # D-22 anti-leak: foreign / unknown account → 404 account_not_found.
        monkeypatch.setattr(
            drafts_service.account_store, "get_by_id_for_user",
            lambda _aid, _uid: None,
        )
        with pytest.raises(AccountNotFound):
            drafts_service.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
                _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_draft_not_found_short_circuits(self, monkeypatch):
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        monkeypatch.setattr(
            drafts_service.draft_store, "get",
            lambda _did, _aid: None,
        )
        with pytest.raises(DraftNotFound):
            drafts_service.copy_attachments_from_email(
                _MAILBOX_ID, _ACCOUNT_ID, "missing", _SOURCE_ACCOUNT_ID,
                _SOURCE_MESSAGE_ID, _USER_ID,
            )

    def test_inline_attachments_filtered_out(self, monkeypatch):
        # Only ``is_inline=False`` rows are candidates; inline images
        # are excluded (D-13 strict + R-06).
        _patch_copy_attachments_common(monkeypatch, draft_provider="gmail")
        rows = [
            {
                "attachment_id": _SOURCE_ATTACHMENT_ID,
                "filename": "inline.png",
                "mime_type": "image/png",
                "size": 100,
                "content_id": "cid-1",
                "is_inline": True,  # excluded
                "position": 0,
                "part_id": "1",
                "provider_attachment_id": None,
                "unavailable_at": None,
            },
        ]
        monkeypatch.setattr(
            drafts_service.email_attachment_store, "list_by_message",
            lambda _aid, _mid: rows,
        )
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_existing_source_attachment_ids",
            lambda _aid, _did: set(),
        )
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "list_by_draft",
            lambda _aid, _did: [],
        )
        inserts: list[dict] = []
        monkeypatch.setattr(
            drafts_service.draft_attachment_store, "insert",
            lambda row: inserts.append(row) or row,
        )
        result = drafts_service.copy_attachments_from_email(
            _MAILBOX_ID, _ACCOUNT_ID, _COPY_DRAFT_ID,
            _SOURCE_ACCOUNT_ID, _SOURCE_MESSAGE_ID, _USER_ID,
        )
        # Inline rows pre-filtered → no copies.
        assert result.copied_count == 0
        assert inserts == []
