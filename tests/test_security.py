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
        changed = answer()
        changed["judgments"]["support:margin"]["note"] = "Changed explanation requiring amendment"
        s.save_snapshot(b, 1, "draft", changed, "Ada")
        with pytest.raises(HTTPError) as e:
            request(
                h,
                "/api/next",
                "POST",
                {"bundle_id": b.bundle_id, "bundle_hash": b.bundle_hash},
                Origin=h.origin,
            )
        assert e.value.code == 409
        s.save_submission(b, 2, "amend", changed, "Ada", 0, "changed")
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


def test_wrong_or_missing_token_and_traversal_variants(tmp_path):
    from urllib.request import Request, urlopen as raw

    b = example_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": h.token}):
            with pytest.raises(HTTPError) as e:
                raw(Request(h.origin + "/api/state", headers=headers))
            assert e.value.code == 403
        for path in ("/%2e%2e/%2e%2e/etc/passwd", "/ui/../server.py", "//etc/passwd", "/app.js/../../store.py"):
            with pytest.raises(HTTPError) as e:
                request(h, path)
            assert e.value.code in (403, 404)


def test_symlinked_state_root_is_refused(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        FileStore(tmp_path / "link")


def test_blinding_markers_refuse_bundle_and_withhold_responses(tmp_path):
    from evidence_review.server import BlindingViolation

    markers = ["arm-secret-7", "Judge-Verdict-Clean"]
    leaked = example_bundle().model_dump(mode="json")
    leaked["sources"][0]["metadata"]["producer"] = "ARM-SECRET-7"
    with pytest.raises(BlindingViolation):
        open_review(leaked, FileStore(tmp_path / "a"), launch=False, blind_markers=markers)
    with pytest.raises(ValueError, match="3 characters"):
        open_review(example_bundle(), FileStore(tmp_path / "b"), launch=False, blind_markers=["A"])

    def leaky_backup(sub):
        return {"status": "succeeded", "identifier": "branch/judge-verdict-clean", "reason": "ok"}

    b = example_bundle()
    with open_review(b, FileStore(tmp_path / "c"), Hooks(on_submission=leaky_backup), launch=False, blind_markers=markers) as h:
        body = {"bundle_id": b.bundle_id, "bundle_hash": b.bundle_hash, "revision": 0, "key": "k", "answers": answer(), "assessor": "Ada"}
        with pytest.raises(HTTPError) as e:
            request(h, "/api/submit", "POST", body, Origin=h.origin)
        assert e.value.code == 500
        message = e.value.read().decode()
        assert "blinding violation" in message and "judge" not in message.lower()


def test_adjudication_may_show_declared_disclosures(tmp_path):
    raw = example_bundle().model_dump(mode="json")
    raw.update(task_kind="adjudication", disclosures={"Assessment A": "arm-secret-7 said supported"})
    with open_review(raw, FileStore(tmp_path), launch=False, blind_markers=["arm-secret-7"]) as h:
        with request(h, "/api/bundle") as r:
            assert "arm-secret-7" in r.read().decode()
