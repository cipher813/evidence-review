"""Fault injection: an acknowledged answer is never lost or duplicated."""

import json
import os
import threading
import time
import pytest
from evidence_review import FileStore, Hooks, export_json, open_review
from evidence_review.example import example_bundle
from evidence_review.hooks import run_hook
from evidence_review.store import Conflict
import evidence_review.store as store_module
from test_store import answer


def fail_once(monkeypatch, name, after_calls=0):
    real = getattr(store_module.os, name)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == after_calls + 1:
            raise OSError(f"injected {name} failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module.os, name, flaky)


@pytest.mark.parametrize("fault", ["wal-fsync", "dir-fsync", "replace"])
@pytest.mark.parametrize("submit", [False, True])
def test_crash_points_recover_without_duplicate(tmp_path, monkeypatch, fault, submit):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    save = s.save_submission if submit else s.save_snapshot
    if fault == "wal-fsync":
        fail_once(monkeypatch, "fsync")  # bytes written, durability unconfirmed
    elif fault == "dir-fsync":
        fail_once(monkeypatch, "fsync", after_calls=1)
    else:
        fail_once(monkeypatch, "replace")  # snapshot rename after WAL commit
    with pytest.raises(OSError, match="injected"):
        save(b, 0, "op-1", answer(), "Ada")
    monkeypatch.undo()
    # Restart: the write-ahead log is authoritative. The request was never
    # acknowledged, but if it reached the log the same key replays it once.
    restarted = FileStore(tmp_path)
    state = restarted.load_task(b.bundle_id)
    assert state["revision"] == 1
    again = save(b, 0, "op-1", answer(), "Ada")
    assert again["revision"] == 1
    assert len(again["submissions"]) == (1 if submit else 0)
    with pytest.raises(Conflict):
        save(b, 0, "op-2", answer(), "Ada")


def test_failure_before_log_write_leaves_nothing(tmp_path, monkeypatch):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    real_open = store_module.os.open

    def refuse(path, flags, *args):
        if str(path).endswith("events.jsonl") and flags & os.O_APPEND:
            raise OSError("injected disk full")
        return real_open(path, flags, *args)

    monkeypatch.setattr(store_module.os, "open", refuse)
    with pytest.raises(OSError):
        s.save_snapshot(b, 0, "op-1", answer(), "Ada")
    monkeypatch.undo()
    assert FileStore(tmp_path).load_task(b.bundle_id)["revision"] == 0


def test_export_is_deterministic_with_provenance(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    s.save_submission(b, 1, "amend", answer(), "Ada", 0, "Rechecked source")
    first = export_json(s, b.bundle_id, 2)
    assert first == export_json(FileStore(tmp_path), b.bundle_id, 2)
    record = json.loads(first)
    assert record["bundle_hash"] == b.bundle_hash
    assert record["submitted_at"].endswith("+00:00")
    assert record["provenance"]["amends_revision"] == "1"
    assert record["provenance"]["submission_schema"] == "review-submission/v1"
    assert record["provenance"]["package_version"]
    assert "amends_revision" not in json.loads(export_json(s, b.bundle_id, 1))["provenance"]


def test_adjudication_requires_reasons(tmp_path):
    raw = example_bundle().model_dump(mode="json")
    raw.update(task_kind="adjudication", bundle_id="adjudicate", disclosures={"Assessment A": "supported"})
    from evidence_review import validate_bundle

    b = validate_bundle(raw)
    s = FileStore(tmp_path)
    s.register(b)
    with pytest.raises(ValueError, match="adjudication reason"):
        s.save_submission(b, 0, "a", answer(), "Ada")
    reasoned = answer()
    reasoned["judgments"]["support:margin"]["note"] = "Both cited lines match the table."
    assert s.save_submission(b, 0, "b", reasoned, "Ada")["last_submission"] == 1


def test_timed_out_hook_is_unknown_and_never_replayed(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    release = threading.Event()
    effects = []

    def slow_backup(sub):
        effects.append(sub["revision"])  # external side effect happens
        release.wait(5)
        return {"status": "succeeded", "identifier": "receipt-1", "reason": "pushed"}

    hooks = Hooks(on_submission=slow_backup, timeout=0.05)
    state = run_hook(s, b.bundle_id, 1, hooks)
    assert state["hook"]["status"] == "unknown"
    assert "reconcile" in state["hook"]["reason"]
    assert run_hook(s, b.bundle_id, 1, hooks)["hook"]["status"] == "unknown"
    assert effects == [1]  # no blind replay
    release.set()
    for _ in range(100):
        if s.load_task(b.bundle_id)["hook"]["status"] != "unknown":
            break
        time.sleep(0.02)
    hook = s.load_task(b.bundle_id)["hook"]
    assert (hook["status"], hook["identifier"]) == ("succeeded", "receipt-1")
    assert hook["reason"].startswith("late result")


def test_late_hook_result_cannot_overwrite_newer_revision(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    release = threading.Event()

    def slow(sub):
        release.wait(5)
        return {"status": "succeeded", "identifier": "old-receipt", "reason": "late"}

    run_hook(s, b.bundle_id, 1, Hooks(on_submission=slow, timeout=0.05))
    s.save_submission(b, 1, "amend", answer(), "Ada", 0, "Corrected")
    release.set()
    time.sleep(0.2)
    hook = s.load_task(b.bundle_id)["hook"]
    assert hook["revision"] == 2 and hook["identifier"] != "old-receipt"


def test_reconcile_without_caller_keeps_unknown_or_failed(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    run_hook(s, b.bundle_id, 1, Hooks(on_submission=lambda sub: {"status": "unknown", "identifier": None, "reason": "remote timeout"}))
    assert s.load_task(b.bundle_id)["hook"]["status"] == "unknown"
    state = run_hook(s, b.bundle_id, 1, Hooks(), reconcile=True)
    assert state["hook"]["status"] == "unknown"
    assert state["hook"]["reason"] == "caller reconciliation required"


def test_navigation_events_are_exposure_not_verification(tmp_path):
    from test_server import request

    b = example_bundle()
    s = FileStore(tmp_path)
    with open_review(b, s, launch=False) as h:
        base = {"bundle_id": b.bundle_id, "bundle_hash": b.bundle_hash}
        request(h, "/api/event", "POST", {**base, "kind": "source_opened", "subject": "demo-source"}, Origin=h.origin)
        request(h, "/api/event", "POST", {**base, "kind": "span_opened", "subject": "summary:0:3"}, Origin=h.origin)
        from urllib.error import HTTPError

        with pytest.raises(HTTPError) as e:
            request(h, "/api/event", "POST", {**base, "kind": "verified"}, Origin=h.origin)
        assert e.value.code == 400
    assert s.activity(b.bundle_id) == {"counts": {"source_opened": 1, "span_opened": 1}, "verification": False}
