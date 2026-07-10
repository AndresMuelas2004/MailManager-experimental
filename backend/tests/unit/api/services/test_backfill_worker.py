"""Unit tests for ``api.services.backfill_worker._run_backfill_job``.

The worker orchestrates a fake manager + faked persistence helpers. Everything
is patched on the ``backfill_worker`` module itself (the patch-target rule:
these names are imported by-name into the worker, so patching the facade would
not intercept the call). No DB, no provider.
"""

from __future__ import annotations

import threading

import pytest

from api.services import backfill_worker as worker
from core.email import BackfillPage
from core.email.errors import EmailAuthError, EmailExternalAPIError
from tests.shared.email_fakes import build_metadata


_RECORD = {"account_id": "acc1", "mailbox_id": "mb1", "provider": "gmail"}
_LABEL = "mb1__acc1"


def _job(**overrides) -> dict:
    job = {
        "account_id": "acc1",
        "mailbox_id": "mb1",
        "provider": "gmail",
        "target_total": 1000,
        "fetched_count": 0,
        "page_cursor": None,
        "initial_sync_cursor": None,
    }
    job.update(overrides)
    return job


class _FakeManager:
    def __init__(
        self,
        *,
        pages=None,
        anchor="anchor1",
        last_errors=None,
        refreshed=None,
        fetch_exc=None,
        anchor_exc=None,
    ):
        self._pages = list(pages or [])
        self._anchor = anchor
        self._last_errors = last_errors or {}
        self._refreshed = refreshed if refreshed is not None else {}
        self._fetch_exc = fetch_exc
        self._anchor_exc = anchor_exc
        self.authenticate_calls = 0
        self.capture_anchor_calls = 0
        self.fetch_calls: list[tuple] = []

    def authenticate_all_silent(self, payloads):
        self.authenticate_calls += 1
        return self._refreshed

    def get_last_errors(self):
        return self._last_errors

    def capture_backfill_anchor(self, label):
        self.capture_anchor_calls += 1
        if self._anchor_exc:
            raise self._anchor_exc
        return self._anchor

    def fetch_backfill_page(self, label, cursor, page_size):
        self.fetch_calls.append((label, cursor, page_size))
        if self._fetch_exc:
            raise self._fetch_exc
        return self._pages.pop(0)


class _RecordingStore:
    def __init__(self, order):
        self._order = order
        self.set_anchor_calls: list[tuple] = []
        self.progress_calls: list[tuple] = []
        self.completed: list[str] = []
        self.failed: list[tuple] = []

    def set_anchor(self, account_id, anchor):
        self.set_anchor_calls.append((account_id, anchor))

    def update_progress(self, account_id, count, cursor):
        self.progress_calls.append((account_id, count, cursor))

    def mark_completed(self, account_id):
        self.completed.append(account_id)
        self._order.append("completed")

    def mark_failed(self, account_id, error):
        self.failed.append((account_id, error))


def _setup(monkeypatch, manager, *, record=_RECORD):
    """Patch the worker's dependencies. Returns (store, calls)."""
    order: list[str] = []
    store = _RecordingStore(order)
    calls: dict = {"persist": [], "tokens": [], "cursor": [], "order": order, "built": []}

    monkeypatch.setattr(worker, "account_store", type("_AS", (), {"get": staticmethod(lambda mid, aid: record)}))
    monkeypatch.setattr(worker, "account_backfill_store", store)

    def _build(accounts):
        calls["built"].append(accounts)
        return manager

    monkeypatch.setattr(worker, "build_manager_for_accounts", _build)
    monkeypatch.setattr(
        worker, "_build_auth_context",
        lambda accounts, mid: ({}, {_LABEL: (mid, "acc1", "gmail")}),
    )
    monkeypatch.setattr(
        worker, "_persist_refreshed_tokens",
        lambda refreshed, lookup, **kw: calls["tokens"].append(refreshed),
    )
    monkeypatch.setattr(
        worker, "persist_email_metadata_batch",
        lambda aid, upserts, **kw: calls["persist"].append((aid, list(upserts))),
    )

    def _cursor(mid, aid, cur, **kw):
        calls["cursor"].append((mid, aid, cur))
        order.append("cursor")

    monkeypatch.setattr(worker, "update_sync_cursor", _cursor)
    return store, calls


def _no_stop() -> threading.Event:
    return threading.Event()


# ===== happy path =====


def test_chains_waves_checkpoints_and_completes(monkeypatch):
    # Two waves: the second exhausts the mailbox (next_cursor=None).
    pages = [
        BackfillPage(upserts=[build_metadata("m1"), build_metadata("m2")], next_cursor="tok2"),
        BackfillPage(upserts=[build_metadata("m3")], next_cursor=None),
    ]
    manager = _FakeManager(pages=pages)
    # Keep Gmail pacing effectively zero so the two-wave test stays fast.
    monkeypatch.setenv("BACKFILL_GMAIL_GETS_PER_MINUTE", "1000000")
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(), _no_stop())

    # Anchor captured once (job had no initial_sync_cursor), before the waves.
    assert manager.capture_anchor_calls == 1
    assert store.set_anchor_calls == [("acc1", "anchor1")]
    # Each wave persisted and checkpointed (count + next cursor).
    assert calls["persist"] == [("acc1", [build_metadata("m1"), build_metadata("m2")]),
                                ("acc1", [build_metadata("m3")])]
    assert store.progress_calls == [("acc1", 2, "tok2"), ("acc1", 3, None)]
    # The paginating caller forwards the previous page's next_cursor.
    assert [c[1] for c in manager.fetch_calls] == [None, "tok2"]
    # Completion: cursor written FIRST (so the next sync is incremental), then
    # the job marked completed (so the sync guard stops excluding it).
    assert calls["cursor"] == [("mb1", "acc1", "anchor1")]
    assert store.completed == ["acc1"]
    assert calls["order"].index("cursor") < calls["order"].index("completed")
    assert store.failed == []


def test_trims_last_wave_to_target_total(monkeypatch):
    # target_total=2 but the page carries 3 → trimmed to the remaining 2, and
    # the target-reached break completes the job.
    pages = [BackfillPage(
        upserts=[build_metadata("m1"), build_metadata("m2"), build_metadata("m3")],
        next_cursor="tok2",
    )]
    manager = _FakeManager(pages=pages)
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(target_total=2), _no_stop())

    assert calls["persist"] == [("acc1", [build_metadata("m1"), build_metadata("m2")])]
    assert store.progress_calls == [("acc1", 2, "tok2")]
    assert store.completed == ["acc1"]


def test_empty_mailbox_completes_at_zero(monkeypatch):
    pages = [BackfillPage(upserts=[], next_cursor=None)]
    manager = _FakeManager(pages=pages)
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(), _no_stop())

    assert calls["persist"] == [("acc1", [])]
    assert store.progress_calls == [("acc1", 0, None)]
    assert store.completed == ["acc1"]


def test_resume_from_page_cursor_without_recapturing_anchor(monkeypatch):
    # A job with an existing checkpoint (page_cursor + initial_sync_cursor)
    # resumes without re-capturing the anchor and reuses the stored cursor.
    pages = [BackfillPage(upserts=[build_metadata("m6")], next_cursor=None)]
    manager = _FakeManager(pages=pages)
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(
        _job(page_cursor="tok1", initial_sync_cursor="existing_anchor", fetched_count=5),
        _no_stop(),
    )

    assert manager.capture_anchor_calls == 0
    assert store.set_anchor_calls == []
    # The first wave resumes from the stored page cursor.
    assert manager.fetch_calls[0][1] == "tok1"
    # fetched_count grows from the checkpoint (5 -> 6).
    assert store.progress_calls == [("acc1", 6, None)]
    # The pre-existing anchor is the one written to accounts.sync_cursor.
    assert calls["cursor"] == [("mb1", "acc1", "existing_anchor")]
    assert store.completed == ["acc1"]


def test_persists_refreshed_tokens_when_returned(monkeypatch):
    manager = _FakeManager(
        pages=[BackfillPage(upserts=[], next_cursor=None)],
        refreshed={_LABEL: {"access_token": "new"}},
    )
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(), _no_stop())

    assert calls["tokens"] == [{_LABEL: {"access_token": "new"}}]


# ===== failure paths =====


def test_wave_failure_exhausts_retries_and_marks_failed(monkeypatch):
    # Drop the wave-retry delays to zero so the retry loop is instant.
    monkeypatch.setattr(worker, "_WAVE_RETRY_DELAYS", (0.0,))
    manager = _FakeManager(fetch_exc=EmailExternalAPIError("provider down"))
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(), _no_stop())

    # Retried up to the configured attempts, then failed with the wave reason.
    assert len(manager.fetch_calls) == worker._WAVE_RETRY_ATTEMPTS
    assert store.failed == [("acc1", "wave_fetch_failed")]
    assert store.completed == []


def test_silent_auth_failure_marks_failed_without_fetching(monkeypatch):
    # authenticate_all_silent does NOT raise — it records the per-account error;
    # the job detects it via get_last_errors and fails without a wave/anchor.
    manager = _FakeManager(last_errors={_LABEL: EmailAuthError("token revoked")})
    store, calls = _setup(monkeypatch, manager)

    worker._run_backfill_job(_job(), _no_stop())

    assert store.failed == [("acc1", "auth")]
    assert manager.capture_anchor_calls == 0
    assert manager.fetch_calls == []
    assert store.completed == []


def test_unexpected_error_is_swallowed_and_marked_failed(monkeypatch):
    manager = _FakeManager(pages=[BackfillPage(upserts=[], next_cursor=None)])
    store, calls = _setup(monkeypatch, manager)
    # Make a step inside the try blow up unexpectedly.
    monkeypatch.setattr(
        worker, "_build_auth_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    # Must not raise — the worker swallows and marks the job failed.
    worker._run_backfill_job(_job(), _no_stop())

    assert store.failed == [("acc1", "unexpected")]
    assert store.completed == []


def test_account_deleted_exits_clean_without_marking(monkeypatch):
    manager = _FakeManager(pages=[BackfillPage(upserts=[], next_cursor=None)])
    store, calls = _setup(monkeypatch, manager, record=None)

    worker._run_backfill_job(_job(), _no_stop())

    # The FK CASCADE already removed the job row → nothing to mark, and the
    # manager is never even built.
    assert store.completed == []
    assert store.failed == []
    assert store.set_anchor_calls == []
    assert calls["built"] == []


def test_shutdown_leaves_job_running_for_resume(monkeypatch):
    # stop_event set before the wave loop → the loop's else-branch runs and the
    # job is left neither completed nor failed (reset_running_to_pending resumes
    # it next start from its checkpoint).
    manager = _FakeManager(pages=[BackfillPage(upserts=[build_metadata("m1")], next_cursor="tok2")])
    store, calls = _setup(monkeypatch, manager)
    stop = threading.Event()
    stop.set()

    worker._run_backfill_job(_job(initial_sync_cursor="anchor"), stop)

    assert store.completed == []
    assert store.failed == []
    # No wave ran while stopping.
    assert manager.fetch_calls == []


# ===== pacing =====


class _FakeStopEvent:
    def __init__(self):
        self.waits: list[float] = []

    def wait(self, seconds):
        self.waits.append(seconds)


class TestPaceAfterWave:
    def test_gmail_sleeps_to_hit_target_rate(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_GMAIL_GETS_PER_MINUTE", "300")
        stop = _FakeStopEvent()
        # 300 gets at 300/min => a full minute of budget; elapsed 0 => wait ~60s.
        worker._pace_after_wave("gmail", 300, 0.0, stop)
        assert stop.waits and abs(stop.waits[0] - 60.0) < 0.01

    def test_gmail_no_sleep_when_wave_already_slow(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_GMAIL_GETS_PER_MINUTE", "300")
        stop = _FakeStopEvent()
        # Elapsed already exceeds the budget → no additional sleep.
        worker._pace_after_wave("gmail", 10, 100.0, stop)
        assert stop.waits == []

    def test_outlook_uses_fixed_page_delay(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_OUTLOOK_PAGE_DELAY_MS", "300")
        stop = _FakeStopEvent()
        worker._pace_after_wave("outlook", 100, 0.0, stop)
        assert stop.waits == [0.3]

    def test_outlook_zero_delay_skips_sleep(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_OUTLOOK_PAGE_DELAY_MS", "0")
        stop = _FakeStopEvent()
        worker._pace_after_wave("outlook", 100, 0.0, stop)
        assert stop.waits == []
