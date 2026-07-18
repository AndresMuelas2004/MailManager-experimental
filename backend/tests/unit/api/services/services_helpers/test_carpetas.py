"""Unit tests for ``services_helpers.carpetas`` (carpetas-y-reglas).

This leaf holds the Provider-First assignment logic shared by the folder
service, the sync-time rule evaluation and the rule-apply worker, so its
invariants are tested once here:

- ``assign_folder_provider_first`` materialises the folder → applies the
  label/category at the provider → and ONLY on provider success adds the local
  member. A provider failure must NOT insert ``email_folder_members``.
- ``ensure_folder_materialized`` reuses a stored link without re-listing the
  provider; only the first use in an account calls the provider + persists.
- ``reconcile_folder_memberships`` is BEST-EFFORT (never raises), no-ops when the
  account has no managed folder, feeds from both upserts and label updates, and
  treats ``provider_labels=None`` as "do not touch".
- ``fetch_folder_chips`` / ``enrich_items_with_folders`` group by
  ``(pmid, account_id)`` and degrade to ``{}`` / empty on any failure.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.errors.exceptions import (
    AccountNotConnected,
    ExternalAPIError,
    FolderOperationError,
)
from api.services.services_helpers import carpetas
from core.email import EmailExternalAPIError


_ACCOUNT_ID = "acc-1"
_LABEL = "mb-1__acc-1"
_FOLDER_ID = "folder-1"
_PMID = "m1"


class _FakeManager:
    def __init__(self, *, assign_exc=None, silent_return=None, last_errors=None):
        self._assign_exc = assign_exc
        self.silent_return = silent_return if silent_return is not None else {}
        self.last_errors = last_errors or {}
        self.ensure_calls: list = []
        self.assign_calls: list = []
        self.unassign_calls: list = []

    def ensure_folder_ref(self, label, name):
        self.ensure_calls.append((label, name))
        return "PROVIDER_REF"

    def assign_folder_to_message(self, label, message_id, provider_ref, name):
        self.assign_calls.append((label, message_id, provider_ref, name))
        if self._assign_exc is not None:
            raise self._assign_exc
        return message_id

    def unassign_folder_from_message(self, label, message_id, provider_ref, name):
        self.unassign_calls.append((label, message_id, provider_ref, name))
        return message_id

    def authenticate_all_silent(self, payloads):
        return self.silent_return

    def get_last_errors(self):
        return self.last_errors


class _FakeFolderStore:
    def __init__(self, *, link=None, ref_map=None, chips_rows=None):
        self._link = link
        self._ref_map = ref_map if ref_map is not None else {}
        self._chips_rows = chips_rows or []
        self.upsert_link_calls: list = []
        self.add_member_calls: list = []
        self.remove_member_calls: list = []
        self.reconcile_calls: list = []

    def get_link(self, folder_id, account_id):
        return self._link

    def upsert_link(self, folder_id, account_id, provider_ref):
        self.upsert_link_calls.append((folder_id, account_id, provider_ref))

    def add_member(self, provider_message_id, account_id, folder_id):
        self.add_member_calls.append((provider_message_id, account_id, folder_id))

    def remove_member(self, provider_message_id, account_id, folder_id):
        self.remove_member_calls.append((provider_message_id, account_id, folder_id))

    def get_ref_map(self, account_id):
        return self._ref_map

    def reconcile_memberships(self, account_id, present, seen_message_ids, managed_folder_ids):
        self.reconcile_calls.append((account_id, present, seen_message_ids, managed_folder_ids))

    def list_folders_for_messages(self, pairs):
        return self._chips_rows


# ---------------------------------------------------------------------------
# assign / unassign Provider-First
# ---------------------------------------------------------------------------


class TestAssignFolderProviderFirst:
    def test_order_materialise_then_provider_then_member(self, monkeypatch):
        store = _FakeFolderStore(link=None)
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager()
        carpetas.assign_folder_provider_first(
            manager, _LABEL, _ACCOUNT_ID, _PMID, _FOLDER_ID, "Universidad",
            fallback=FolderOperationError,
        )
        # First-use materialisation calls the provider + persists the link.
        assert manager.ensure_calls == [(_LABEL, "Universidad")]
        assert store.upsert_link_calls == [(_FOLDER_ID, _ACCOUNT_ID, "PROVIDER_REF")]
        # Provider apply used the materialised ref, THEN the member was added.
        assert manager.assign_calls == [(_LABEL, _PMID, "PROVIDER_REF", "Universidad")]
        assert store.add_member_calls == [(_PMID, _ACCOUNT_ID, _FOLDER_ID)]

    def test_provider_failure_does_not_insert_member(self, monkeypatch):
        store = _FakeFolderStore(link={"provider_ref": "PROVIDER_REF"})
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager(assign_exc=EmailExternalAPIError("provider down"))
        with pytest.raises(ExternalAPIError):
            carpetas.assign_folder_provider_first(
                manager, _LABEL, _ACCOUNT_ID, _PMID, _FOLDER_ID, "Universidad",
                fallback=FolderOperationError,
            )
        # The whole point of Provider-First: no local membership on provider fail.
        assert store.add_member_calls == []


class TestEnsureFolderMaterialized:
    def test_existing_link_skips_provider(self, monkeypatch):
        store = _FakeFolderStore(link={"provider_ref": "Label_99"})
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager()
        ref = carpetas.ensure_folder_materialized(
            manager, _LABEL, _ACCOUNT_ID, _FOLDER_ID, "Work", fallback=FolderOperationError,
        )
        assert ref == "Label_99"
        assert manager.ensure_calls == []
        assert store.upsert_link_calls == []

    def test_first_use_calls_provider_and_persists(self, monkeypatch):
        store = _FakeFolderStore(link=None)
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager()
        ref = carpetas.ensure_folder_materialized(
            manager, _LABEL, _ACCOUNT_ID, _FOLDER_ID, "Work", fallback=FolderOperationError,
        )
        assert ref == "PROVIDER_REF"
        assert manager.ensure_calls == [(_LABEL, "Work")]
        assert store.upsert_link_calls == [(_FOLDER_ID, _ACCOUNT_ID, "PROVIDER_REF")]


class TestUnassignFolderProviderFirst:
    def test_removes_provider_then_member_when_materialised(self, monkeypatch):
        store = _FakeFolderStore(link={"provider_ref": "Label_1"})
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager()
        carpetas.unassign_folder_provider_first(
            manager, _LABEL, _ACCOUNT_ID, _PMID, _FOLDER_ID, "Work", fallback=FolderOperationError,
        )
        assert manager.unassign_calls == [(_LABEL, _PMID, "Label_1", "Work")]
        assert store.remove_member_calls == [(_PMID, _ACCOUNT_ID, _FOLDER_ID)]

    def test_no_link_skips_provider_but_removes_member(self, monkeypatch):
        store = _FakeFolderStore(link=None)
        monkeypatch.setattr(carpetas, "folder_store", store)
        manager = _FakeManager()
        carpetas.unassign_folder_provider_first(
            manager, _LABEL, _ACCOUNT_ID, _PMID, _FOLDER_ID, "Work", fallback=FolderOperationError,
        )
        assert manager.unassign_calls == []
        assert store.remove_member_calls == [(_PMID, _ACCOUNT_ID, _FOLDER_ID)]


# ---------------------------------------------------------------------------
# reconcile_folder_memberships (best-effort)
# ---------------------------------------------------------------------------


class TestReconcileFolderMemberships:
    def test_no_managed_folders_short_circuits(self, monkeypatch):
        store = _FakeFolderStore(ref_map={})
        monkeypatch.setattr(carpetas, "folder_store", store)
        carpetas.reconcile_folder_memberships(
            _ACCOUNT_ID,
            [SimpleNamespace(provider_message_id=_PMID, provider_labels=["Label_1"])],
            [],
        )
        assert store.reconcile_calls == []

    def test_crosses_provider_labels_against_managed_refs(self, monkeypatch):
        store = _FakeFolderStore(ref_map={"Label_1": _FOLDER_ID})
        monkeypatch.setattr(carpetas, "folder_store", store)
        upserts = [SimpleNamespace(provider_message_id=_PMID, provider_labels=["Label_1", "SYSTEM"])]
        carpetas.reconcile_folder_memberships(_ACCOUNT_ID, upserts, [])
        assert len(store.reconcile_calls) == 1
        _aid, present, seen, managed = store.reconcile_calls[0]
        # Only the managed label maps to a present pair; the system label is dropped.
        assert present == [(_PMID, _FOLDER_ID)]
        assert seen == [_PMID]
        assert managed == [_FOLDER_ID]

    def test_label_update_with_none_labels_is_not_seen(self, monkeypatch):
        store = _FakeFolderStore(ref_map={"Label_1": _FOLDER_ID})
        monkeypatch.setattr(carpetas, "folder_store", store)
        # A partial Outlook delta with no categories → provider_labels None →
        # "do not touch": the message is not in ``seen`` so nothing is deleted.
        label_updates = [SimpleNamespace(provider_message_id="m2", provider_labels=None)]
        carpetas.reconcile_folder_memberships(_ACCOUNT_ID, [], label_updates)
        assert store.reconcile_calls == []

    def test_swallows_store_error(self, monkeypatch):
        class _Boom(_FakeFolderStore):
            def get_ref_map(self, account_id):
                raise RuntimeError("db down")

        monkeypatch.setattr(carpetas, "folder_store", _Boom())
        # Best-effort: must NOT raise (runs after the sync response is sent).
        carpetas.reconcile_folder_memberships(
            _ACCOUNT_ID, [SimpleNamespace(provider_message_id=_PMID, provider_labels=["x"])], [],
        )


# ---------------------------------------------------------------------------
# folder chips
# ---------------------------------------------------------------------------


class TestFolderChips:
    def test_empty_pairs_returns_empty_without_store(self, monkeypatch):
        # A store that raises would prove the short-circuit skips it.
        class _NeverStore(_FakeFolderStore):
            def list_folders_for_messages(self, pairs):
                raise AssertionError("must not be called for empty pairs")

        monkeypatch.setattr(carpetas, "folder_store", _NeverStore())
        assert carpetas.fetch_folder_chips([]) == {}

    def test_groups_rows_by_message_and_account(self, monkeypatch):
        rows = [
            {"provider_message_id": _PMID, "account_id": _ACCOUNT_ID, "folder_id": "f1", "name": "A", "color": "#111"},
            {"provider_message_id": _PMID, "account_id": _ACCOUNT_ID, "folder_id": "f2", "name": "B", "color": None},
        ]
        monkeypatch.setattr(carpetas, "folder_store", _FakeFolderStore(chips_rows=rows))
        chips = carpetas.fetch_folder_chips([(_PMID, _ACCOUNT_ID)])
        refs = chips[(_PMID, _ACCOUNT_ID)]
        assert [r.folder_id for r in refs] == ["f1", "f2"]
        assert refs[0].name == "A"

    def test_store_failure_degrades_to_empty(self, monkeypatch):
        class _Boom(_FakeFolderStore):
            def list_folders_for_messages(self, pairs):
                raise RuntimeError("db down")

        monkeypatch.setattr(carpetas, "folder_store", _Boom())
        assert carpetas.fetch_folder_chips([(_PMID, _ACCOUNT_ID)]) == {}

    def test_enrich_items_sets_folders_in_place(self, monkeypatch):
        rows = [{"provider_message_id": _PMID, "account_id": _ACCOUNT_ID, "folder_id": "f1", "name": "A", "color": None}]
        monkeypatch.setattr(carpetas, "folder_store", _FakeFolderStore(chips_rows=rows))
        items = [SimpleNamespace(provider_message_id=_PMID, account_id=_ACCOUNT_ID, folders=[])]
        carpetas.enrich_items_with_folders(items)
        assert [r.folder_id for r in items[0].folders] == ["f1"]


# ---------------------------------------------------------------------------
# authenticate_single_account
# ---------------------------------------------------------------------------


class TestAuthenticateSingleAccount:
    def _wire(self, monkeypatch, manager):
        monkeypatch.setattr(carpetas, "build_manager_for_accounts", lambda _records: manager)
        monkeypatch.setattr(carpetas, "load_wrapped_app_credentials", lambda _p: {})
        monkeypatch.setattr(carpetas, "load_wrapped_account_tokens", lambda *a: {})

    def test_happy_path_returns_manager_and_label(self, monkeypatch):
        manager = _FakeManager(silent_return={})
        self._wire(monkeypatch, manager)
        monkeypatch.setattr(carpetas, "raise_on_silent_auth_errors", lambda errors, *, fallback: None)
        result_manager, label = carpetas.authenticate_single_account(
            {"provider": "gmail", "account_id": _ACCOUNT_ID}, "mb-1", fallback=FolderOperationError,
        )
        assert result_manager is manager
        assert label == _LABEL

    def test_silent_auth_failure_surfaces_as_account_not_connected(self, monkeypatch):
        manager = _FakeManager(last_errors={_LABEL: Exception("dead token")})
        self._wire(monkeypatch, manager)

        def _raise(errors, *, fallback):
            raise AccountNotConnected("dead token")

        monkeypatch.setattr(carpetas, "raise_on_silent_auth_errors", _raise)
        with pytest.raises(AccountNotConnected):
            carpetas.authenticate_single_account(
                {"provider": "gmail", "account_id": _ACCOUNT_ID}, "mb-1", fallback=FolderOperationError,
            )
