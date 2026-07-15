"""Tests espejo de ``emails_service.favoritos``: reconciliacion de ids y sync_favorites."""

from __future__ import annotations

from datetime import datetime, timezone

from api.services.emails_service import _comunes, favoritos
from core.email import EmailManager, FavoriteCandidate
from tests.shared.email_fakes import FakeEmailClient, build_favorite_candidate

from ._helpers import (
    _ACCOUNT_ID,
    _LABEL,
    _MAILBOX_ID,
    _USER_ID,
    _fake_account,
)


_DT = datetime(2025, 1, 1, 10, 0)


def _stored_row(
    provider_message_id, *, received_at=_DT, from_email="sender@example.com", subject="subject",
):
    """A ``list_metadata_identity_for_account`` row (identity columns only)."""
    return {
        "provider_message_id": provider_message_id,
        "received_at": received_at,
        "from_email": from_email,
        "subject": subject,
    }


# ── _reconcile_favorite_ids (pure-ish helper, mocked DB read) ──────────


class TestReconcileFavoriteIds:
    def test_gmail_shaped_candidates_short_circuit_without_db_read(self, monkeypatch):
        # No candidate carries identity fields (Gmail — ids already stable) →
        # passthrough WITHOUT touching the DB at all.
        def _explode(_aid, **_kw):
            raise AssertionError("identity read must not run when no candidate carries identity")
        monkeypatch.setattr(favoritos, "load_account_identity_metadata", _explode)
        candidates = [
            FavoriteCandidate(provider_message_id="g1"),
            FavoriteCandidate(provider_message_id="g2"),
        ]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["g1", "g2"]

    def test_reconciles_drifted_outlook_id_to_stored_stable_id(self, monkeypatch):
        # Outlook: $filter=flag/flagStatus returns 'D' for a message already
        # stored under the stable id 'A' (same physical identity) → remap.
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [_stored_row("A")],
        )
        candidates = [build_favorite_candidate(provider_message_id="D", received_at=_DT)]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["A"]

    def test_candidate_own_id_already_stored_is_left_untouched(self, monkeypatch):
        # Resolved via the SAME identity-lookup path as any other candidate
        # (no "my own id is already stored" shortcut) — it still resolves to
        # its own id because that IS its physical-identity match.
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [_stored_row("G1")],
        )
        candidates = [build_favorite_candidate(provider_message_id="G1", received_at=_DT)]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["G1"]

    def test_candidate_with_no_match_kept_verbatim(self, monkeypatch):
        # A genuinely new favourite (not yet synced locally) has no physical
        # match — kept verbatim, later dropped by the full-replacement UPDATE
        # (already-documented "Option A" skip behaviour).
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [_stored_row("A")],
        )
        candidates = [build_favorite_candidate(
            provider_message_id="N", received_at=datetime(2025, 2, 2, 8, 0),
            from_email="other@test.com", subject="Different",
        )]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["N"]

    def test_deterministic_with_duplicate_stored_rows(self, monkeypatch):
        # Past syncs left duplicate rows for one physical message. Rows arrive
        # ordered (received_at DESC, provider_message_id), so the first match
        # (min provider_message_id) wins deterministically.
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [_stored_row("id-aaa"), _stored_row("id-bbb"), _stored_row("id-ccc")],
        )
        candidates = [build_favorite_candidate(provider_message_id="D", received_at=_DT)]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["id-aaa"]

    def test_read_failure_degrades_to_verbatim_ids(self, monkeypatch):
        # A reconciliation read failure must degrade to verbatim ids (logged)
        # rather than aborting the whole account's sync.
        def _raise(_aid, **_kw):
            raise RuntimeError("db read failed")
        monkeypatch.setattr(favoritos, "load_account_identity_metadata", _raise)
        candidates = [build_favorite_candidate(provider_message_id="D", received_at=_DT)]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["D"]

    def test_candidate_id_colliding_with_unrelated_stored_row_resolves_by_identity(
        self, monkeypatch,
    ):
        # Live-verified edge case against a real Outlook account: a
        # candidate's raw id can coincide with an UNRELATED stored row's id
        # (e.g. two sibling messages of the same thread a second apart).
        # There must be no "my own id is already stored, skip the lookup"
        # shortcut — trusting that coincidence would apply the favourite to
        # the WRONG row. The candidate's identity matches 'A' (not 'SIBLING',
        # whose id happens to equal the candidate's own raw id).
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [
                _stored_row("A", received_at=datetime(2025, 1, 1, 10, 0, 4)),
                _stored_row("SIBLING", received_at=datetime(2025, 1, 1, 10, 0, 3)),
            ],
        )
        candidates = [build_favorite_candidate(
            provider_message_id="SIBLING", received_at=datetime(2025, 1, 1, 10, 0, 4),
        )]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["A"]

    def test_mixed_candidates_multiple_outcomes(self, monkeypatch):
        g1_received_at = datetime(2025, 6, 6, 6, 0)
        monkeypatch.setattr(
            favoritos, "load_account_identity_metadata",
            lambda _aid, **_kw: [_stored_row("A"), _stored_row("G1", received_at=g1_received_at)],
        )
        candidates = [
            build_favorite_candidate(provider_message_id="D", received_at=_DT),
            build_favorite_candidate(provider_message_id="G1", received_at=g1_received_at),
            build_favorite_candidate(
                provider_message_id="N", received_at=datetime(2099, 1, 1),
                from_email="x@y.com", subject="z",
            ),
        ]
        assert favoritos._reconcile_favorite_ids(_ACCOUNT_ID, candidates) == ["A", "G1", "N"]


# ── sync_favorites (full service call through a fake EmailManager) ─────


def _patch_sync_favorites_common(
    monkeypatch,
    *,
    accounts=None,
    fake_client_kwargs_by_label=None,
    identity_rows_by_account=None,
    identity_read_exc=None,
    sync_calls=None,
    sync_return_by_account=None,
):
    """Common monkeypatches for ``sync_favorites`` tests.

    ``accounts`` defaults to a single Outlook account (the drift-prone
    provider). ``fake_client_kwargs_by_label`` maps ``f"{mailbox}__{account}"``
    to the ``FakeEmailClient`` kwargs for that account (typically
    ``list_favorite_candidates_return``). ``identity_rows_by_account`` maps
    account_id to the rows ``load_account_identity_metadata`` returns
    (default: no rows). ``sync_calls`` records each ``(account_id,
    favorite_ids)`` passed to ``sync_favorites_for_account``.
    """
    accounts = accounts if accounts is not None else [_fake_account(provider="outlook")]
    monkeypatch.setattr(
        favoritos, "ensure_mailbox_access",
        lambda _mb, _uid: {"mailbox_id": _MAILBOX_ID, "owner_user_id": _USER_ID},
    )
    by_id = {a["account_id"]: a for a in accounts}
    monkeypatch.setattr(favoritos.account_store, "get", lambda _mb, aid: by_id.get(aid))
    monkeypatch.setattr(favoritos.account_store, "list_by_mailbox", lambda _mb: list(accounts))
    monkeypatch.setattr(
        _comunes, "load_wrapped_app_credentials",
        lambda _prov: {"client_id": "cid", "client_secret": "cs"},
    )
    monkeypatch.setattr(
        _comunes, "load_wrapped_account_tokens",
        lambda _mb, _acc, _prov: {"access_token": "at", "refresh_token": "rt"},
    )
    monkeypatch.setattr(favoritos.account_store, "upsert_tokens", lambda *_a, **_kw: None)

    kwargs_by_label = fake_client_kwargs_by_label or {}

    def _build(built_accounts):
        manager = EmailManager()
        for acc in built_accounts:
            mid = str(acc.get("mailbox_id", ""))
            aid = str(acc.get("account_id", ""))
            label = f"{mid}__{aid}"
            manager.add_client(FakeEmailClient(
                label,
                auth_return={"access_token": "tok", "refresh_token": "ref"},
                **kwargs_by_label.get(label, {}),
            ))
        return manager
    monkeypatch.setattr(favoritos, "build_manager_for_accounts", _build)

    _identity_rows = identity_rows_by_account or {}
    if identity_read_exc is not None:
        def _load_identity(_aid, **_kw):
            raise identity_read_exc
    else:
        def _load_identity(aid, **_kw):
            return list(_identity_rows.get(aid, []))
    monkeypatch.setattr(favoritos, "load_account_identity_metadata", _load_identity)

    _returns = sync_return_by_account or {}

    def _sync(aid, ids):
        if sync_calls is not None:
            sync_calls.append((aid, list(ids)))
        return _returns.get(aid, len(ids))
    monkeypatch.setattr(favoritos.email_metadata_store, "sync_favorites_for_account", _sync)


class TestSyncFavorites:
    def test_outlook_reconciles_drifted_id_before_persisting(self, monkeypatch):
        candidates = [build_favorite_candidate(
            provider_message_id="D", received_at=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        )]
        sync_calls: list = []
        _patch_sync_favorites_common(
            monkeypatch,
            fake_client_kwargs_by_label={_LABEL: {"list_favorite_candidates_return": candidates}},
            identity_rows_by_account={_ACCOUNT_ID: [_stored_row(
                "A", received_at=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            )]},
            sync_calls=sync_calls,
        )
        favoritos.sync_favorites(_MAILBOX_ID, _USER_ID, account_id=_ACCOUNT_ID)
        # The full-replacement UPDATE must run with the STABLE id 'A', never
        # the drifted 'D' — otherwise it silently wipes the stored favourite.
        assert sync_calls == [(_ACCOUNT_ID, ["A"])]

    def test_gmail_candidates_pass_through_without_touching_identity_store(self, monkeypatch):
        candidates = [FavoriteCandidate(provider_message_id="g1")]
        sync_calls: list = []

        def _explode(_aid, **_kw):
            raise AssertionError("identity read must not run for Gmail-shaped candidates")
        _patch_sync_favorites_common(
            monkeypatch,
            accounts=[_fake_account(provider="gmail")],
            fake_client_kwargs_by_label={_LABEL: {"list_favorite_candidates_return": candidates}},
            sync_calls=sync_calls,
        )
        monkeypatch.setattr(favoritos, "load_account_identity_metadata", _explode)
        favoritos.sync_favorites(_MAILBOX_ID, _USER_ID, account_id=_ACCOUNT_ID)
        assert sync_calls == [(_ACCOUNT_ID, ["g1"])]

    def test_identity_read_failure_degrades_to_verbatim_and_does_not_abort(self, monkeypatch):
        candidates = [build_favorite_candidate(provider_message_id="D", received_at=_DT)]
        sync_calls: list = []
        _patch_sync_favorites_common(
            monkeypatch,
            fake_client_kwargs_by_label={_LABEL: {"list_favorite_candidates_return": candidates}},
            identity_read_exc=RuntimeError("db read failed"),
            sync_calls=sync_calls,
        )
        result = favoritos.sync_favorites(_MAILBOX_ID, _USER_ID, account_id=_ACCOUNT_ID)
        assert sync_calls == [(_ACCOUNT_ID, ["D"])]
        assert result.accounts[0].account_id == _ACCOUNT_ID

    def test_favorites_synced_counts_candidates_not_resolved_ids(self, monkeypatch):
        # favorites_synced reports what the provider claims (candidate count),
        # unaffected by how many candidates got remapped vs. kept verbatim.
        candidates = [
            build_favorite_candidate(provider_message_id="D1", received_at=datetime(2025, 1, 1, 10, 0)),
            build_favorite_candidate(provider_message_id="D2", received_at=datetime(2025, 1, 2, 10, 0)),
        ]
        _patch_sync_favorites_common(
            monkeypatch,
            fake_client_kwargs_by_label={_LABEL: {"list_favorite_candidates_return": candidates}},
        )
        result = favoritos.sync_favorites(_MAILBOX_ID, _USER_ID, account_id=_ACCOUNT_ID)
        assert result.accounts[0].favorites_synced == 2
