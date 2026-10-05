"""A second, unrelated consumer uses only the exported API.

It runs in an isolated interpreter so no test helper, research adapter or
provider SDK can leak in, and it fails if one is imported.
"""

import subprocess
import sys
import textwrap

CONSUMER = textwrap.dedent(
    r'''
    import json, sys, tempfile, urllib.request
    import evidence_review as er
    from evidence_review import FileStore, Hooks, export_json, inventory, open_review, validate_bundle
    from evidence_review.contracts import digest

    policy = "Section 2. Refunds are issued within 30 days.\nSection 3. Fees are non-refundable.\n"
    memo = "Customers receive refunds within 30 days, including fees."
    receipts = []
    bundle = validate_bundle({
        "bundle_id": "policy-memo-1",
        "task_kind": "reference",
        "document_hashes": {"memo": digest(memo), "rubric": digest("policy-rubric-v3")},
        "fields": [{"path": "memo", "label": "Memo", "text": memo}],
        "sources": [{"source_id": "policy", "title": "Refund policy", "text": policy, "sha256": digest(policy)}],
        "spans": [{"span_id": "memo:33:35", "field_path": "memo", "start": 33, "end": 35, "text": "30", "state": "uncited"}],
        "claims": [],
        "references": [
            {"reference_id": "fees", "text": "Fees are excluded from refunds.",
             "citations": [{"source_id": "policy", "start_line": 2, "end_line": 2, "excerpt": "Fees are non-refundable.", "status": "located"}]}
        ],
        "form": [
            {"field_id": "fees", "label": "Is the fee exclusion stated correctly?", "kind": "choice",
             "options": ["addressed", "missing", "contradicted", "uncertain"], "subject_id": "fees",
             "note_required_unless": ["addressed"], "evidence_required": True},
            {"field_id": "done", "label": "Memo fully reviewed", "kind": "boolean", "require_true": True},
        ],
    })
    assert inventory(bundle)["span_total"] == 1

    def backup(sub):
        receipts.append(sub["revision"])
        return {"status": "succeeded", "identifier": "archive-%d" % sub["revision"], "reason": "stored"}

    state = tempfile.mkdtemp()
    with open_review(bundle, FileStore(state), Hooks(on_submission=backup), launch=False) as h:
        def post(path, body):
            req = urllib.request.Request(h.origin + path, json.dumps(body).encode(), method="POST",
                headers={"Authorization": "Bearer " + h.token, "Origin": h.origin, "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req))
        answers = {"judgments": {
            "fees": {"value": "contradicted", "note": "Memo includes fees; policy excludes them.",
                     "selections": [{"source_id": "policy", "source_hash": digest(policy), "start_line": 2, "end_line": 2,
                                     "excerpt": "Section 3. Fees are non-refundable.", "subject_id": "fees"}]},
            "done": {"value": True}}, "defects": []}
        base = {"bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash, "assessor": "Synthetic"}
        post("/api/save", {**base, "revision": 0, "key": "s1", "answers": answers})
        result = post("/api/submit", {**base, "revision": 1, "key": "s2", "answers": answers})
        assert result["hook"]["status"] == "succeeded", result["hook"]
    record = json.loads(export_json(FileStore(state), bundle.bundle_id, 2))
    assert record["judgments"]["fees"]["value"] == "contradicted"
    assert record["provenance"]["rubric"] == digest("policy-rubric-v3")
    assert receipts == [2]
    forbidden = [m for m in sys.modules if m.split(".")[0] in
                 {"primer_eval", "anthropic", "openai", "boto3", "google", "requests", "httpx"}]
    assert not forbidden, forbidden
    print("consumer-ok")
    '''
)


def test_second_consumer_uses_only_exported_api(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-c", CONSUMER], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "consumer-ok"


def test_public_api_is_declared_and_versioned():
    import tomllib
    from pathlib import Path
    import evidence_review

    for name in evidence_review.__all__:
        assert hasattr(evidence_review, name), name
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert evidence_review.__version__ == project["project"]["version"]


def test_cli_inspect_and_export(tmp_path, capsys):
    import json
    import pytest
    from evidence_review import FileStore
    from evidence_review.cli import main
    from evidence_review.example import example_bundle
    from test_store import answer

    bundle = tmp_path / "bundle.json"
    b = example_bundle()
    bundle.write_text(json.dumps(b.model_dump(mode="json")))
    assert main(["inspect", "--bundle", str(bundle)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["spans_by_state"] == {"derived": 2, "uncited": 1}
    state = tmp_path / "state"
    s = FileStore(state)
    s.register(b)
    s.save_submission(b, 0, "submit", answer(), "Ada")
    assert main(["export", "--state-dir", str(state), "--task", b.bundle_id, "--revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
    for argv in (
        ["export", "--state-dir", str(state), "--task", b.bundle_id, "--revision", "9"],
        ["export", "--state-dir", str(state), "--task", "missing", "--revision", "1"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
