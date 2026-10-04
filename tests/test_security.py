import json
import pytest
from urllib.error import HTTPError
from test_server import request
from evidence_review import open_review, validate_bundle
from evidence_review.example import example_bundle
from evidence_review.store import FileStore, Conflict
from evidence_review.hooks import Hooks, run_hook
from test_store import answer


def test_pending_hook_requires_caller_reconciliation(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    s.hook_status(
        b.bundle_id,
        1,
        {"status": "pending", "identifier": None, "reason": "unknown", "invoked": True},
    )
    calls = []
    hooks = Hooks(on_submission=lambda sub: calls.append(sub))
    assert run_hook(s, b.bundle_id, 1, hooks)["hook"]["status"] == "pending"
    assert calls == []
    assert (
        run_hook(s, b.bundle_id, 1, hooks, reconcile=True)["hook"]["status"]
        == "pending"
    )


def test_draft_after_submission_blocks_next_and_old_hook_cannot_replace_amendment(
    tmp_path,
):
    b = example_bundle()
    s = FileStore(tmp_path)
    with open_review(b, s, launch=False) as h:
        s.save_submission(b, 0, "submit", answer(), "Ada")
        run_hook(s, b.bundle_id, 1)
        s.save_snapshot(b, 1, "draft", answer(), "Ada")
        with pytest.raises(HTTPError) as e:
            request(
                h,
                "/api/next",
                "POST",
                {"bundle_id": b.bundle_id, "bundle_hash": b.bundle_hash},
                Origin=h.origin,
            )
        assert e.value.code == 409
        s.save_submission(b, 2, "amend", answer(), "Ada", 0, "changed")
        with pytest.raises(Conflict):
            s.hook_status(
                b.bundle_id, 1, {"status": "succeeded", "identifier": "stale"}
            )


def test_independent_disclosures_and_unknown_ids_refused():
    raw = example_bundle().model_dump(mode="json")
    raw["disclosures"] = {"judge": "clean"}
    with pytest.raises(ValueError, match="independent"):
        validate_bundle(raw)


def test_oversize_body_and_symlink_snapshot_refused(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    with open_review(b, s, launch=False) as h:
        from http.client import HTTPConnection

        conn = HTTPConnection("127.0.0.1", h.server.server_port)
        conn.request(
            "POST",
            "/api/save",
            headers={
                "Authorization": "Bearer " + h.token,
                "Origin": h.origin,
                "Content-Type": "application/json",
                "Content-Length": "2000001",
            },
        )
        assert conn.getresponse().status == 400
        conn.close()
    snapshot = tmp_path / b.bundle_id / "snapshot.json"
    snapshot.unlink()
    snapshot.symlink_to(tmp_path / "target")
    with pytest.raises(ValueError, match="symlink"):
        s.save_snapshot(b, 0, "save", answer(), "Ada")
    # WAL persisted before snapshot failure; authoritative recovery exposes it.
    assert s.load_task(b.bundle_id)["revision"] == 1
