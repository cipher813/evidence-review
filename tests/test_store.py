"""Missing WAL/revision checks would lose acknowledged answers or silently overwrite."""

from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from evidence_review.store import FileStore, Conflict
from evidence_review.example import example_bundle


def answer():
    return {
        "judgments": {
            "support:margin": {"value": "supported"},
            "report_complete": {"value": True},
        },
        "defects": [],
    }


def test_restart_idempotency_and_stale_tab(tmp_path):
    b = example_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    first = store.save_snapshot(b, 0, "answer-1", answer(), "Ada", 2)
    assert first["revision"] == 1
    assert (
        FileStore(tmp_path).load_task(b.bundle_id)["answers"]["judgments"][
            "support:margin"
        ]["value"]
        == "supported"
    )
    assert store.save_snapshot(b, 0, "answer-1", answer(), "Ada", 2)["revision"] == 1
    with pytest.raises(Conflict):
        store.save_snapshot(b, 0, "other", answer(), "Ada", 2)
    with pytest.raises(Conflict):
        store.save_snapshot(b, 1, "answer-1", {}, "Ada", 2)


def test_complete_amendment_keeps_prior_revision(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    with pytest.raises(ValueError, match="required"):
        s.save_submission(b, 0, "empty", {}, "Ada", 0)
    first = s.save_submission(b, 0, "submit", answer(), "Ada", 3)
    assert s.export_submission(b.bundle_id, first["revision"]).complete
    with pytest.raises(ValueError, match="amendment"):
        s.save_submission(b, 1, "silent", answer(), "Ada", 1)
    amended = s.save_submission(b, 1, "amend", answer(), "Ada", 1, "Checked again")
    assert amended["revision"] == 2
    assert s.export_submission(b.bundle_id, 1).amendment_reason == ""
    assert s.export_submission(b.bundle_id, 2).amendment_reason == "Checked again"


def test_wal_replay_and_concurrent_writers(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)

    def write(key):
        try:
            return s.save_snapshot(b, 0, key, answer(), "Ada", 1)["revision"]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["one", "two"]))
    assert sorted(map(str, results)) == ["1", "conflict"]
    # Snapshot disappears between event fsync and rename: acknowledged state replays.
    (tmp_path / b.bundle_id / "snapshot.json").unlink()
    assert FileStore(tmp_path).load_task(b.bundle_id)["revision"] == 1
    with open(tmp_path / b.bundle_id / "events.jsonl", "ab") as f:
        f.write(b'{"torn":')
    assert FileStore(tmp_path).load_task(b.bundle_id)["revision"] == 1
    assert (
        FileStore(tmp_path).save_snapshot(b, 1, "after-recovery", answer(), "Ada", 1)[
            "revision"
        ]
        == 2
    )
    assert FileStore(tmp_path).load_task(b.bundle_id)["revision"] == 2


def test_changed_bundle_and_symlink_state_refused(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    changed = b.model_copy(update={"document_hashes": {"report": "changed"}})
    with pytest.raises(Conflict):
        s.register(changed)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        s.load_task("evil")


def test_invalid_verdict_and_forged_source_selection(tmp_path):
    b = example_bundle()
    s = FileStore(tmp_path)
    s.register(b)
    a = answer()
    a["judgments"]["support:margin"]["value"] = "clean"
    with pytest.raises(ValueError, match="option"):
        s.save_snapshot(b, 0, "bad", a, "Ada", 1)
    a = answer()
    a["judgments"]["support:margin"]["selections"] = [
        {
            "source_id": "demo-source",
            "source_hash": b.sources[0].sha256,
            "start_line": 4,
            "end_line": 4,
            "excerpt": "forged",
        }
    ]
    with pytest.raises(ValueError, match="selection"):
        s.save_snapshot(b, 0, "bad2", a, "Ada", 1)


def test_required_boolean_no_is_valid_but_completion_must_be_true(tmp_path):
    from evidence_review.contracts import FormField

    b = example_bundle()
    b.form.append(
        FormField(field_id="is_accurate", label="Is it accurate?", kind="boolean")
    )
    s = FileStore(tmp_path)
    s.register(b)
    a = answer()
    a["judgments"]["is_accurate"] = {"value": False}
    s.save_submission(b, 0, "no", a, "Ada")
    assert s.export_submission(b.bundle_id, 1).judgments["is_accurate"].value is False
    a["judgments"]["report_complete"]["value"] = False
    with pytest.raises(ValueError, match="required"):
        s.save_submission(b, 1, "incomplete", a, "Ada", 0, "revise")

def test_optional_unanswered_annotation_is_preserved_but_not_a_verdict(tmp_path):
    b = example_bundle()
    b.form[0].required = False
    b.form[0].note_required_unless = ["supported"]
    b.form[0].evidence_required = True
    s = FileStore(tmp_path)
    s.register(b)
    a = answer()
    a["judgments"]["support:margin"] = {"value": "", "claim_ids": [b.claims[0].claim_id]}
    s.save_submission(b, 0, "optional", a, "Ada")
    saved = s.export_submission(b.bundle_id, 1).judgments["support:margin"]
    assert saved.value == ""
    assert saved.claim_ids == [b.claims[0].claim_id]
    a["judgments"]["support:margin"]["value"] = "invalid"
    with pytest.raises(ValueError, match="option"):
        s.save_submission(b, 1, "invalid", a, "Ada", 0, "check")
    a["judgments"]["support:margin"] = {"value": "", "claim_ids": ["forged"]}
    with pytest.raises(ValueError, match="unknown selected claim"):
        s.save_submission(b, 1, "forged", a, "Ada", 0, "check")
    a["judgments"]["support:margin"] = {"value": "", "selections": [{
        "source_id": b.sources[0].source_id, "source_hash": b.sources[0].sha256,
        "start_line": 4, "end_line": 4, "excerpt": "forged",
    }]}
    with pytest.raises(ValueError, match="selection"):
        s.save_submission(b, 1, "forged-evidence", a, "Ada", 0, "check")
