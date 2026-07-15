"""Unit tests for ``api.services.backfill_worker._run_backfill_job``.

The worker orchestrates a fake manager + faked persistence helpers. Everything
is patched on the ``backfill_worker`` module itself (the patch-target rule:
these names are imported by-name into the worker, so patching the facade would
not intercept the call). No DB, no provider.
"""

from __future__ import annotations

import threading

import pytest

from api.errors.exceptions import DatabaseConnectionError, DraftSyncError
from api.services import backfill_worker as worker
from core.email import BackfillPage, DraftMetadata
from core.email.errors import EmailAuthError, EmailExternalAPIError
from database import ConnectionPoolError
from tests.shared.email_fakes import DEFAULT_RECEIVED_AT, build_metadata


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


# ===== _is_pool_exhaustion (recognises raw + translated + chained) =====


class TestIsPoolExhaustion:
    def test_raw_connection_pool_error(self):
        assert worker._is_pool_exhaustion(ConnectionPoolError("pool empty")) is True

    def test_translated_database_connection_error(self):
        # persist_email_metadata_batch translates ConnectionPoolError into
        # DatabaseConnectionError, so the detector must accept that form too.
        assert worker._is_pool_exhaustion(DatabaseConnectionError("pool empty")) is True

    def test_walks_the_cause_chain(self):
        try:
            try:
                raise ConnectionPoolError("pool empty")
            except ConnectionPoolError as inner:
                raise RuntimeError("wrapper") from inner
        except RuntimeError as exc:
            assert worker._is_pool_exhaustion(exc) is True

    def test_unrelated_error_is_not_pool_exhaustion(self):
        assert worker._is_pool_exhaustion(ValueError("nope")) is False


# ===== _gated_db_write (concurrency gate + brief pool-exhaustion retry) =====


class TestGatedDbWrite:
    def test_returns_fn_result_without_a_gate(self):
        # _DB_WRITE_GATE is None in a direct unit call → _gate() is a nullcontext.
        assert worker._gated_db_write(lambda: 42, _no_stop()) == 42

    def test_non_pool_error_propagates_immediately_without_retry(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            raise ValueError("not a pool problem")

        stop = _FakeStopEvent()
        with pytest.raises(ValueError):
            worker._gated_db_write(_fn, stop)
        # A non-pool error is re-raised on the first attempt — no retry, no wait.
        assert calls["n"] == 1
        assert stop.waits == []

    def test_retries_on_pool_exhaustion_then_succeeds(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionPoolError("pool empty")
            return "ok"

        stop = _FakeStopEvent()
        assert worker._gated_db_write(_fn, stop) == "ok"
        assert calls["n"] == 2
        # One wait between the failed attempt and the successful retry.
        assert len(stop.waits) == 1

    def test_exhausts_retries_and_raises_the_pool_error(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            raise ConnectionPoolError("pool empty")

        stop = _FakeStopEvent()
        with pytest.raises(ConnectionPoolError):
            worker._gated_db_write(_fn, stop)
        assert calls["n"] == worker._DB_WRITE_RETRY_ATTEMPTS
        # Waits between every attempt except the last.
        assert len(stop.waits) == worker._DB_WRITE_RETRY_ATTEMPTS - 1


# ===== _wave_retry_after (best-effort Retry-After from the exc detail) =====


def test_wave_retry_constants_are_hardened():
    # Robustness bump 3 -> 5 attempts with the (2,5,10,20) backoff. Pin the
    # values so a silent revert (which would halve the retry budget against a
    # throttling provider) fails loudly here.
    assert worker._WAVE_RETRY_ATTEMPTS == 5
    assert worker._WAVE_RETRY_DELAYS == (2.0, 5.0, 10.0, 20.0)


class TestWaveRetryAfter:
    def _exc(self, detail):
        exc = EmailExternalAPIError("throttled")
        exc.detail = detail
        return exc

    def test_numeric_retry_after_is_returned_as_float(self):
        assert worker._wave_retry_after(self._exc({"retry_after": 5})) == 5.0

    def test_zero_or_negative_retry_after_is_ignored(self):
        assert worker._wave_retry_after(self._exc({"retry_after": 0})) is None

    def test_non_numeric_retry_after_is_ignored(self):
        assert worker._wave_retry_after(self._exc({"retry_after": "5"})) is None

    def test_missing_key_returns_none(self):
        assert worker._wave_retry_after(self._exc({})) is None

    def test_no_detail_returns_none(self):
        assert worker._wave_retry_after(EmailExternalAPIError("boom")) is None


# ===== _reap_retriable_failed (revives failed backfill + draft jobs) =====


class _FakeReapStore:
    def __init__(self, *, exc=None, revived=0):
        self._exc = exc
        self._revived = revived
        self.reset_calls: list[tuple] = []

    def reset_retriable_failed_to_pending(self, max_attempts, backoff_seconds):
        self.reset_calls.append((max_attempts, backoff_seconds))
        if self._exc:
            raise self._exc
        return self._revived


class TestReapRetriableFailed:
    def test_reaps_both_backfill_and_draft_stores(self, monkeypatch):
        monkeypatch.setattr(worker, "backfill_max_attempts", lambda: 5)
        backfill = _FakeReapStore(revived=1)
        draft = _FakeReapStore(revived=2)
        monkeypatch.setattr(worker, "account_backfill_store", backfill)
        monkeypatch.setattr(worker, "draft_sync_store", draft)

        worker._reap_retriable_failed()

        # Both queues are reaped every poll with (max_attempts, cool-off).
        assert backfill.reset_calls == [(5, worker._FAILED_RETRY_BACKOFF_S)]
        assert draft.reset_calls == [(5, worker._FAILED_RETRY_BACKOFF_S)]

    def test_a_store_failure_does_not_stall_the_other(self, monkeypatch):
        monkeypatch.setattr(worker, "backfill_max_attempts", lambda: 5)
        backfill = _FakeReapStore(exc=RuntimeError("boom"))
        draft = _FakeReapStore(revived=0)
        monkeypatch.setattr(worker, "account_backfill_store", backfill)
        monkeypatch.setattr(worker, "draft_sync_store", draft)

        # Must not raise — a reaper failure is swallowed so the poll continues.
        worker._reap_retriable_failed()
        # The draft store was still reaped despite the backfill store failing.
        assert draft.reset_calls == [(5, worker._FAILED_RETRY_BACKOFF_S)]


# ===== _run_draft_sync_job (auth → fetch_all_drafts → replace → complete) =====


def _draft_job(**overrides) -> dict:
    job = {"account_id": "acc1", "mailbox_id": "mb1", "provider": "gmail"}
    job.update(overrides)
    return job


class _FakeDraftManager:
    def __init__(self, *, drafts=None, auth_error=None, fetch_error=None, refreshed=None):
        self._drafts = drafts if drafts is not None else {}
        self._last_errors: dict = {}
        if auth_error is not None:
            self._last_errors[_LABEL] = auth_error
        self._fetch_error = fetch_error
        self._refreshed = refreshed if refreshed is not None else {}
        self.fetch_drafts_calls = 0

    def authenticate_all_silent(self, payloads):
        return self._refreshed

    def get_last_errors(self):
        return self._last_errors

    def fetch_all_drafts(self):
        self.fetch_drafts_calls += 1
        if self._fetch_error is not None:
            self._last_errors[_LABEL] = self._fetch_error
        return self._drafts


def _setup_draft(monkeypatch, manager, *, record=_RECORD):
    calls: dict = {"replace": [], "completed": [], "failed": [], "built": []}
    monkeypatch.setattr(
        worker, "account_store",
        type("_AS", (), {"get": staticmethod(lambda mid, aid: record)}),
    )
    monkeypatch.setattr(
        worker, "draft_store",
        type("_DS", (), {
            "replace_all_for_account":
                staticmethod(lambda aid, rows: calls["replace"].append((aid, rows))),
        }),
    )
    monkeypatch.setattr(
        worker, "draft_sync_store",
        type("_DSS", (), {
            "mark_completed": staticmethod(lambda aid: calls["completed"].append(aid)),
            "mark_failed": staticmethod(lambda aid, err: calls["failed"].append((aid, err))),
        }),
    )
    monkeypatch.setattr(
        worker, "build_manager_for_accounts",
        lambda accounts: (calls["built"].append(accounts), manager)[1],
    )
    monkeypatch.setattr(
        worker, "_build_auth_context",
        lambda accounts, mid: ({}, {_LABEL: (mid, "acc1", "gmail")}),
    )
    monkeypatch.setattr(worker, "_persist_refreshed_tokens", lambda *a, **k: None)
    return calls


def _draft(provider_draft_id="d1") -> DraftMetadata:
    return DraftMetadata(
        provider_draft_id=provider_draft_id,
        to_recipients=["to@example.com"],
        cc_recipients=[],
        bcc_recipients=[],
        subject="Subj",
        body="<p>b</p>",
        created_at=DEFAULT_RECEIVED_AT,
        updated_at=DEFAULT_RECEIVED_AT,
    )


class TestRunDraftSyncJob:
    def test_happy_path_replaces_then_marks_completed(self, monkeypatch):
        manager = _FakeDraftManager(drafts={_LABEL: [_draft("d1"), _draft("d2")]})
        calls = _setup_draft(monkeypatch, manager)

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        assert manager.fetch_drafts_calls == 1
        # The provider drafts were mapped (build_draft_rows) and atomically
        # replaced, then the job marked completed. Never failed.
        aid, rows = calls["replace"][0]
        assert aid == "acc1"
        assert [r["provider_draft_id"] for r in rows] == ["d1", "d2"]
        assert calls["completed"] == ["acc1"]
        assert calls["failed"] == []

    def test_empty_draft_list_still_replaces_and_completes(self, monkeypatch):
        # An account with zero drafts atomically replaces to an empty set (a
        # locally-orphaned draft deleted on the provider is cleared).
        manager = _FakeDraftManager(drafts={_LABEL: []})
        calls = _setup_draft(monkeypatch, manager)

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        assert calls["replace"] == [("acc1", [])]
        assert calls["completed"] == ["acc1"]

    def test_silent_auth_failure_marks_failed_without_fetching(self, monkeypatch):
        manager = _FakeDraftManager(auth_error=EmailAuthError("token revoked"))
        calls = _setup_draft(monkeypatch, manager)

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        assert calls["failed"] == [("acc1", "auth")]
        assert manager.fetch_drafts_calls == 0
        assert calls["completed"] == []

    def test_fetch_failure_marks_failed(self, monkeypatch):
        # fetch_all_drafts records the error in _last_errors (it does not raise),
        # so the job detects it via get_last_errors and fails with reason "fetch".
        manager = _FakeDraftManager(fetch_error=EmailExternalAPIError("graph 500"))
        calls = _setup_draft(monkeypatch, manager)

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        assert calls["failed"] == [("acc1", "fetch")]
        assert calls["replace"] == []
        assert calls["completed"] == []

    def test_account_deleted_exits_clean_without_marking(self, monkeypatch):
        manager = _FakeDraftManager(drafts={_LABEL: []})
        calls = _setup_draft(monkeypatch, manager, record=None)

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        # The FK CASCADE already removed the job row → nothing to mark, manager
        # never built.
        assert calls["completed"] == []
        assert calls["failed"] == []
        assert calls["built"] == []

    def test_unexpected_error_is_swallowed_and_marked_failed(self, monkeypatch):
        manager = _FakeDraftManager(drafts={_LABEL: []})
        calls = _setup_draft(monkeypatch, manager)
        monkeypatch.setattr(
            worker, "_build_auth_context",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        worker._run_draft_sync_job(_draft_job(), _no_stop())

        assert calls["failed"] == [("acc1", "unexpected")]
        assert calls["completed"] == []


# ===== _dispatcher_loop + start/stop lifecycle =====
#
# The dispatcher is driven directly in the test thread (never via
# start_backfill_worker) with a fake pool that runs nothing, so no real job
# threads spawn. Iterations are bounded by a reap stub that sets the stop event.


class _ImmediateFuture:
    def __init__(self, done=True):
        self._done = done

    def done(self):
        return self._done


class _RecordingPool:
    """Stand-in for ThreadPoolExecutor: records submissions and runs nothing
    (deterministic — no real threads). ``submit`` returns a future that reports
    done()/still-running per ``future_done``."""

    def __init__(self, future_done=True):
        self._future_done = future_done
        self.submitted: list[tuple] = []
        self.shutdown_called = False

    def submit(self, fn, *args):
        self.submitted.append((fn, args))
        return _ImmediateFuture(self._future_done)

    def shutdown(self, wait=False):
        self.shutdown_called = True


class _ResetStore:
    def __init__(self):
        self.reset_running_calls = 0

    def reset_running_to_pending(self):
        self.reset_running_calls += 1


def _patch_dispatcher(monkeypatch, *, future_done=True, max_concurrent=5):
    """Wire a deterministic dispatcher: fake reset stores, a recording pool,
    zero poll, gate concurrency 1. Returns the reset stores + the created-pool
    list. Restores the module-global ``_DB_WRITE_GATE`` to None afterwards (the
    loop reassigns it)."""
    backfill_store = _ResetStore()
    draft_store_fake = _ResetStore()
    monkeypatch.setattr(worker, "account_backfill_store", backfill_store)
    monkeypatch.setattr(worker, "draft_sync_store", draft_store_fake)
    monkeypatch.setattr(worker, "_DB_WRITE_GATE", None)
    monkeypatch.setattr(worker, "backfill_poll_interval_s", lambda: 0)
    monkeypatch.setattr(worker, "backfill_max_concurrent", lambda: max_concurrent)
    monkeypatch.setattr(worker, "backfill_db_write_concurrency", lambda: 1)

    pools: list[_RecordingPool] = []

    def _make_pool(**_kw):
        pool = _RecordingPool(future_done=future_done)
        pools.append(pool)
        return pool

    monkeypatch.setattr(worker, "ThreadPoolExecutor", _make_pool)
    return {"backfill_store": backfill_store, "draft_store": draft_store_fake, "pools": pools}


def test_dispatcher_loop_resets_running_claims_draft_first_and_shuts_down(monkeypatch):
    state = _patch_dispatcher(monkeypatch, max_concurrent=5)
    stop = threading.Event()

    reap_calls = {"n": 0}

    def _reap():
        reap_calls["n"] += 1
        stop.set()  # one full iteration, then the while-guard ends the loop

    monkeypatch.setattr(worker, "_reap_retriable_failed", _reap)

    claimed = {"draft_limit": None, "backfill_limit": None}

    def _claim_draft(limit):
        claimed["draft_limit"] = limit
        return [{"account_id": "d-acc"}]

    def _claim_backfill(limit):
        claimed["backfill_limit"] = limit
        return [{"account_id": "b-acc"}]

    monkeypatch.setattr(worker, "_claim_draft_jobs", _claim_draft)
    monkeypatch.setattr(worker, "_claim_backfill_jobs", _claim_backfill)

    worker._dispatcher_loop(stop)

    # Startup recovery reset BOTH queues exactly once (running -> pending).
    assert state["backfill_store"].reset_running_calls == 1
    assert state["draft_store"].reset_running_calls == 1
    # The DB-write gate is initialised inside the loop (None at import).
    assert worker._DB_WRITE_GATE is not None
    assert reap_calls["n"] == 1
    # Drafts are claimed BEFORE backfill (they stay responsive), and the backfill
    # claim sees the reduced free budget (5 - 1 draft already submitted = 4).
    assert claimed["draft_limit"] == 5
    assert claimed["backfill_limit"] == 4
    pool = state["pools"][0]
    assert [fn for fn, _a in pool.submitted] == [worker._run_draft_sync_job, worker._run_backfill_job]
    # In-flight waves are abandoned on exit (their checkpoint resumes them).
    assert pool.shutdown_called is True


def test_dispatcher_loop_skips_account_already_in_flight(monkeypatch):
    # A backfill job whose future is still running (done()=False) must NOT be
    # re-submitted while in flight — the dedup guard (account_id in in_flight).
    state = _patch_dispatcher(monkeypatch, future_done=False, max_concurrent=5)
    stop = threading.Event()

    reap_calls = {"n": 0}

    def _reap():
        reap_calls["n"] += 1
        if reap_calls["n"] >= 2:
            stop.set()  # exit after the SECOND iteration

    monkeypatch.setattr(worker, "_reap_retriable_failed", _reap)
    monkeypatch.setattr(worker, "_claim_draft_jobs", lambda _limit: [])
    # The same account is claimable every poll; the guard must submit it once.
    monkeypatch.setattr(worker, "_claim_backfill_jobs", lambda _limit: [{"account_id": "b-acc"}])

    worker._dispatcher_loop(stop)

    assert reap_calls["n"] == 2
    pool = state["pools"][0]
    backfill_submits = [a for fn, a in pool.submitted if fn is worker._run_backfill_job]
    # Submitted once across two iterations despite being claimable both times.
    assert len(backfill_submits) == 1


def test_dispatcher_loop_iteration_error_does_not_kill_the_thread(monkeypatch):
    # An exception in the loop body (here a claim raising) must be swallowed so
    # the sole dispatcher thread survives and keeps polling.
    state = _patch_dispatcher(monkeypatch, max_concurrent=2)
    stop = threading.Event()

    reap_calls = {"n": 0}

    def _reap():
        reap_calls["n"] += 1
        if reap_calls["n"] >= 2:
            stop.set()

    monkeypatch.setattr(worker, "_reap_retriable_failed", _reap)

    def _boom(_limit):
        raise RuntimeError("claim boom")

    monkeypatch.setattr(worker, "_claim_draft_jobs", _boom)
    monkeypatch.setattr(worker, "_claim_backfill_jobs", _boom)

    # Must NOT raise despite the claim throwing on every iteration.
    worker._dispatcher_loop(stop)

    # Two iterations ran — the error in iteration 1 did not tumble the loop.
    assert reap_calls["n"] == 2
    assert state["pools"][0].shutdown_called is True


def test_start_worker_disabled_does_not_spawn_thread(monkeypatch):
    monkeypatch.setattr(worker, "is_backfill_worker_enabled", lambda: False)
    monkeypatch.setattr(worker, "_dispatcher_thread", None)
    monkeypatch.setattr(worker, "_stop_event", None)

    worker.start_backfill_worker()

    assert worker._dispatcher_thread is None


def test_start_and_stop_worker_lifecycle(monkeypatch):
    monkeypatch.setattr(worker, "is_backfill_worker_enabled", lambda: True)
    monkeypatch.setattr(worker, "_dispatcher_thread", None)
    monkeypatch.setattr(worker, "_stop_event", None)

    started = threading.Event()

    def _fake_loop(stop_event):
        started.set()
        stop_event.wait()  # block until stop_backfill_worker signals

    monkeypatch.setattr(worker, "_dispatcher_loop", _fake_loop)

    worker.start_backfill_worker()
    try:
        assert started.wait(timeout=2.0)
        thread = worker._dispatcher_thread
        assert thread is not None and thread.is_alive()
        worker.stop_backfill_worker()
        # stop signalled + joined the thread and cleared the module globals.
        assert not thread.is_alive()
        assert worker._dispatcher_thread is None
    finally:
        worker.stop_backfill_worker()


def test_start_worker_idempotent_when_already_running(monkeypatch):
    # A second start with a live dispatcher is a no-op — it must not replace the
    # running thread.
    monkeypatch.setattr(worker, "is_backfill_worker_enabled", lambda: True)

    class _AliveThread:
        def is_alive(self):
            return True

    sentinel = _AliveThread()
    monkeypatch.setattr(worker, "_dispatcher_thread", sentinel)
    monkeypatch.setattr(worker, "_stop_event", threading.Event())
    monkeypatch.setattr(worker, "_dispatcher_loop", lambda *_a, **_k: None)

    worker.start_backfill_worker()

    assert worker._dispatcher_thread is sentinel
