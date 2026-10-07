import json
import threading
import time
import pytest
from evidence_review.example import example_bundle
from evidence_review.store import Conflict, FileStore
import evidence_review.store as store_module
from evidence_review.hooks import Hooks, run_hook
from test_store import answer


def test_hook_is_immutable_idempotent_and_failure_stays_visible(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    state = s.save_submission(b, 0, "submit", answer(), "Ada")
    calls = []

    def success(submission):
        calls.append(submission["revision"])
        with pytest.raises(TypeError):
            submission["assessor"] = "Changed"
        with pytest.raises(TypeError):
            submission["judgments"]["report_complete"]["value"] = False
        return {"status": "succeeded", "identifier": "record-1", "reason": "stored"}

    assert (
        run_hook(s, b.bundle_id, 1, Hooks(on_submission=success))["hook"]["status"]
        == "succeeded"
    )
    run_hook(s, b.bundle_id, 1, Hooks(on_submission=success))
    assert calls == [1]
    amended = s.save_submission(b, 1, "amend", answer(), "Ada", 0, "revision")

    def fail(submission):
        raise OSError("backup unavailable")

    result = run_hook(s, b.bundle_id, 2, Hooks(on_revision=fail))
    assert result["hook"]["status"] == "failed"
    assert "backup unavailable" in result["hook"]["reason"]
    assert s.export_submission(b.bundle_id, 2).complete


def _submitted(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    return b, s


def _race(stores, call):
    """Hold every caller at its first state read until all have read, so each
    decides from the same initial state; returns results and errors."""
    gate = threading.Barrier(len(stores))
    for store in stores:
        original = store.load_task
        seen = threading.local()

        def load(task_id, original=original, seen=seen):
            state = original(task_id)
            if not getattr(seen, "done", False):
                seen.done = True
                gate.wait(timeout=5)
            return state

        store.load_task = load
    results, errors = [], []

    def worker(store):
        try:
            results.append(call(store))
        except Exception as exc:  # pragma: no cover - asserted empty below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(st,)) for st in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results, errors


def test_competing_callers_on_two_stores_invoke_callback_once(tmp_path):
    b, _ = _submitted(tmp_path)
    calls = []

    def callback(sub):
        calls.append(sub["revision"])
        time.sleep(0.05)
        return {"status": "succeeded", "identifier": "receipt", "reason": ""}

    hooks = Hooks(on_submission=callback)
    results, errors = _race(
        [FileStore(tmp_path), FileStore(tmp_path)],
        lambda st: run_hook(st, b.bundle_id, 1, hooks),
    )
    assert errors == [] and len(results) == 2
    assert calls == [1]
    hook = FileStore(tmp_path).load_task(b.bundle_id)["hook"]
    assert (hook["status"], hook["attempt"]) == ("succeeded", 1)


def test_competing_reconciliations_run_once(tmp_path):
    b, s = _submitted(tmp_path)

    def failed(sub):
        raise OSError("backup unavailable")

    assert run_hook(s, b.bundle_id, 1, Hooks(on_submission=failed))["hook"]["status"] == "failed"
    checks = []

    def check_receipt(sub):
        checks.append(sub["revision"])
        time.sleep(0.05)
        return {"status": "succeeded", "identifier": "receipt-2", "reason": "receipt found"}

    hooks = Hooks(on_submission=failed, reconcile=check_receipt)
    results, errors = _race(
        [FileStore(tmp_path), FileStore(tmp_path)],
        lambda st: run_hook(st, b.bundle_id, 1, hooks, reconcile=True),
    )
    assert errors == [] and len(results) == 2
    assert checks == [1]
    hook = s.load_task(b.bundle_id)["hook"]
    assert (hook["status"], hook["identifier"], hook["attempt"]) == ("succeeded", "receipt-2", 2)


def test_reconcile_waits_while_an_attempt_is_within_its_deadline(tmp_path):
    b, s = _submitted(tmp_path)
    started, release = threading.Event(), threading.Event()
    checks = []

    def slow(sub):
        started.set()
        release.wait(5)
        return {"status": "succeeded", "identifier": "receipt-1", "reason": ""}

    hooks = Hooks(on_submission=slow, reconcile=lambda sub: checks.append(sub), timeout=5)
    worker = threading.Thread(target=run_hook, args=(s, b.bundle_id, 1, hooks))
    worker.start()
    assert started.wait(5)
    state = run_hook(FileStore(tmp_path), b.bundle_id, 1, hooks, reconcile=True)
    assert state["hook"]["status"] == "pending" and checks == []
    release.set()
    worker.join(5)
    assert s.load_task(b.bundle_id)["hook"]["identifier"] == "receipt-1"


def test_late_result_cannot_overwrite_newer_attempt(tmp_path):
    b, s = _submitted(tmp_path)
    release = threading.Event()

    def slow(sub):
        release.wait(5)
        return {"status": "succeeded", "identifier": "attempt-1-receipt", "reason": "late"}

    def no_receipt(sub):
        return {"status": "unknown", "identifier": None, "reason": "no receipt yet"}

    hooks = Hooks(on_submission=slow, reconcile=no_receipt, timeout=0.05)
    first = run_hook(s, b.bundle_id, 1, hooks)["hook"]
    assert (first["status"], first["attempt"]) == ("unknown", 1)
    newer = run_hook(s, b.bundle_id, 1, hooks, reconcile=True)["hook"]
    assert (newer["status"], newer["attempt"]) == ("unknown", 2)
    release.set()
    time.sleep(0.3)
    hook = s.load_task(b.bundle_id)["hook"]
    assert (hook["attempt"], hook["identifier"], hook["reason"]) == (2, None, "no receipt yet")
    # The store itself refuses a stale attempt, and a stale claim on the current attempt.
    state, recorded = s.settle_hook(
        b.bundle_id, 1, 1, first["claim"], {"status": "succeeded", "identifier": "x", "reason": ""}, late=True
    )
    assert not recorded and state["hook"]["attempt"] == 2
    _, recorded = s.settle_hook(
        b.bundle_id, 1, 2, first["claim"], {"status": "succeeded", "identifier": "x", "reason": ""}, late=True
    )
    assert not recorded


def test_restart_after_claim_does_not_blindly_replay(tmp_path, monkeypatch):
    b, s = _submitted(tmp_path)
    # A process claims the attempt and dies before the callback returns.
    _, claim = s.claim_hook(b.bundle_id, 1, 0, lease=60)
    assert claim
    effects, checks = [], []
    hooks = Hooks(
        on_submission=lambda sub: effects.append(sub),
        reconcile=lambda sub: checks.append(sub) or {"status": "succeeded", "identifier": "receipt", "reason": ""},
    )
    restarted = FileStore(tmp_path)
    assert run_hook(restarted, b.bundle_id, 1, hooks)["hook"]["status"] == "pending"
    # Within the dead attempt's deadline reconciliation is not claimed either.
    assert run_hook(restarted, b.bundle_id, 1, hooks, reconcile=True)["hook"]["status"] == "pending"
    assert effects == [] and checks == []
    # Once the deadline passes, reconciliation (never the original callback) runs once.
    real_time = store_module.time.time
    monkeypatch.setattr(store_module.time, "time", lambda: real_time() + 120)
    state = run_hook(FileStore(tmp_path), b.bundle_id, 1, hooks, reconcile=True)
    assert state["hook"]["status"] == "succeeded" and state["hook"]["attempt"] == 2
    assert effects == [] and len(checks) == 1
    # The dead attempt's claim no longer owns the record if that process reappears.
    _, recorded = restarted.settle_hook(b.bundle_id, 1, 1, claim, {"status": "failed", "identifier": None, "reason": "stale"})
    assert not recorded


def test_claims_refuse_stale_revisions_and_legacy_hooks_still_reconcile(tmp_path):
    b, s = _submitted(tmp_path)
    s.save_submission(b, 1, "amend", answer(), "Ada", 0, "revision")
    with pytest.raises(Conflict):
        s.claim_hook(b.bundle_id, 1, 0)
    with pytest.raises(Conflict):
        s.settle_hook(b.bundle_id, 1, 1, "claim", {"status": "failed", "identifier": None, "reason": ""})
    with pytest.raises(Conflict):
        s.require_reconciliation(b.bundle_id, 1, "x")
    # A hook record written before attempt claims existed (no attempt, claim or lease).
    s.hook_status(b.bundle_id, 2, {"status": "unknown", "identifier": None, "reason": "old", "invoked": True})
    checks = []
    hooks = Hooks(reconcile=lambda sub: checks.append(sub) or {"status": "succeeded", "identifier": "r", "reason": ""})
    assert run_hook(s, b.bundle_id, 2, hooks)["hook"]["status"] == "unknown"
    assert s.claim_hook(b.bundle_id, 2, 0)[1] is None  # invoked: no initial replay
    state = run_hook(s, b.bundle_id, 2, hooks, reconcile=True)
    assert (state["hook"]["status"], state["hook"]["attempt"], len(checks)) == ("succeeded", 1, 1)
    assert s.require_reconciliation(b.bundle_id, 2, "x")["hook"]["reason"] == ""
    assert s.claim_hook(b.bundle_id, 2, 1, reconcile=True) == (s.load_task(b.bundle_id), None)


def _late_outcome(tmp_path, settle):
    """Time out one attempt, then let its late result meet a store whose settle behaves as given."""
    b, s = _submitted(tmp_path)
    release = threading.Event()

    def slow(sub):
        release.wait(5)
        return {"status": "succeeded", "identifier": "late-receipt", "reason": "late"}

    state = run_hook(s, b.bundle_id, 1, Hooks(on_submission=slow, timeout=0.05))
    assert state["hook"]["status"] == "unknown"
    s.settle_hook = settle(s.settle_hook)
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and not any(
        k.startswith("hook_late") for k in s.activity(b.bundle_id)["counts"]
    ):
        time.sleep(0.02)
    return b, s


def test_late_settlement_io_failure_is_a_durable_sanitized_event(tmp_path):
    def broken(_):
        def settle(*args, **kwargs):
            raise OSError("disk full while writing secret-ish payload text")
        return settle

    b, s = _late_outcome(tmp_path, broken)
    events = [e["detail"] for e in s._events(s.root / b.bundle_id) if e.get("event") == "external"]
    failed = [e for e in events if e["kind"] == "hook_late_settlement_failed"]
    assert len(failed) == 1
    assert failed[0] | {} == {**failed[0], "error_class": "OSError", "reconciliation_required": True,
                              "revision": 1, "attempt": 1, "verification": False}
    assert "disk full" not in json.dumps(events) and "late-receipt" not in json.dumps(events)
    hook = s.load_task(b.bundle_id)["hook"]
    assert hook["status"] == "unknown" and hook["identifier"] is None  # Never promoted, never replayed.


def test_stale_fenced_late_result_is_distinct_from_failure(tmp_path):
    def fenced(original):
        def settle(*args, **kwargs):
            state, _ = original(*args[:3], "not-the-claim", *args[4:], **kwargs)
            return state, False
        return settle

    b, s = _late_outcome(tmp_path, fenced)
    counts = s.activity(b.bundle_id)["counts"]
    assert counts.get("hook_late_result_fenced") == 1 and "hook_late_settlement_failed" not in counts
    assert s.load_task(b.bundle_id)["hook"]["status"] == "unknown"


def test_unpersistable_late_failure_is_kept_in_process_and_logged(tmp_path, caplog):
    from evidence_review.hooks import unpersisted_late_failures

    def broken(_):
        def settle(*args, **kwargs):
            raise OSError("no space")
        return settle

    b, s = _submitted(tmp_path)
    release = threading.Event()
    run_hook(s, b.bundle_id, 1, Hooks(on_submission=lambda sub: release.wait(5) and {"status": "succeeded", "identifier": "r", "reason": ""},
                                      timeout=0.05))
    s.settle_hook = broken(s.settle_hook)

    def no_event(*args, **kwargs):
        raise OSError("event log unavailable")
    s.append_event = no_event
    before = len(unpersisted_late_failures())
    with caplog.at_level("ERROR", logger="evidence_review.hooks"):
        release.set()
        deadline = time.time() + 5
        while time.time() < deadline and len(unpersisted_late_failures()) == before:
            time.sleep(0.02)
    record = unpersisted_late_failures()[-1]
    assert record["kind"] == "hook_late_settlement_failed" and record["event_error_class"] == "OSError"
    assert "not persisted" in caplog.text and "no space" not in caplog.text


def test_late_callback_exception_is_recorded_without_success(tmp_path):
    b, s = _submitted(tmp_path)
    release = threading.Event()

    def explode(sub):
        release.wait(5)
        raise RuntimeError("remote said something private")

    run_hook(s, b.bundle_id, 1, Hooks(on_submission=explode, timeout=0.05))
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and "hook_late_callback_error" not in s.activity(b.bundle_id)["counts"]:
        time.sleep(0.02)
    events = [e["detail"] for e in s._events(s.root / b.bundle_id) if e.get("event") == "external"]
    assert [e["error_class"] for e in events] == ["RuntimeError"]
    assert "private" not in json.dumps(events)
    assert s.load_task(b.bundle_id)["hook"]["status"] == "unknown"
