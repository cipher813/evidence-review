"""answer-annotation/v1: reviewer-selected exact answer passages. Every value is synthetic.

Offsets are Unicode code points. Annotations are optional and omitted from
serialization when empty, so records made before the contract keep their bytes.
"""
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_review import FileStore, answer_range, capabilities, export_json, validate_answer_range
from evidence_review.contracts import (AnswerAnnotation, AnswerRange, Defect, FormField, ReviewBundle,
                                       ReviewSubmission, canonical_json, digest, documents_digest)
from evidence_review.store import submission_matches_snapshot, validate_answers
from synthetic import build, margin_claim

ROOT = Path(__file__).parents[1]
# An astral digit, a decomposed accent (e + U+0301) and one sentence said twice.
QUALIFICATION = "Region 𝟙 note: café is café. Demand was stable. Demand was stable."
FORM = [
    FormField(field_id="support:margin", label="Support", options=["supported", "unsupported"], subject_id="margin"),
    FormField(field_id="report_complete", label="I reviewed the full report.", kind="boolean", require_true=True),
]


def bundle(qualification=QUALIFICATION):
    b = build([("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"]),
                  ("qualifications", qualification, []),
                  ("question", "What changed?", [])],
                 [margin_claim()],
                 {("summary", "180 bps"): ("derived", ["margin"]), ("summary", "24.6%"): ("cited", ["margin"])},
                 form=FORM)
    d = b.model_dump(mode="json")
    d["fields"][2]["role"] = "context"
    return ReviewBundle.model_validate(d)


def second(text, phrase):
    return text.index(phrase, text.index(phrase) + 1)


def annotation(b, start, end, ident="ann:one", **kw):
    return AnswerAnnotation(annotation_id=ident, answer_range=answer_range(b, "qualifications", start, end), **kw)


def complete_answers(annotations=(), defects=()):
    return {"judgments": {"support:margin": {"value": "supported"}, "report_complete": {"value": True}},
            "defects": [d.model_dump(mode="json") if hasattr(d, "model_dump") else d for d in defects],
            "annotations": [a.model_dump(mode="json") for a in annotations]}


# --- offsets ------------------------------------------------------------------------------------------

def test_offsets_are_code_points_for_astral_and_combining_characters():
    b = bundle()
    start = second(QUALIFICATION, "Demand was stable.")
    r = answer_range(b, "qualifications", start, start + len("Demand was stable."))
    assert r.offset_unit == "code_point" and r.text == "Demand was stable."
    # Python str indexing counts code points: the astral digit is one, the accent its own.
    assert QUALIFICATION[7] == "𝟙" and len("𝟙") == 1 and len("𝟙".encode("utf-16-le")) == 4
    accent = QUALIFICATION.index("́")
    assert answer_range(b, "qualifications", accent - 4, accent + 1).text == "café"
    # A range may split a base letter from its combining mark; the exact code points are kept.
    assert answer_range(b, "qualifications", accent - 4, accent).text == "cafe"
    assert r.field_sha256 == digest(QUALIFICATION) and r.documents_sha256 == documents_digest(b.document_hashes)


def test_repeated_text_binds_the_chosen_occurrence():
    b = bundle()
    phrase = "Demand was stable."
    first, again = QUALIFICATION.index(phrase), second(QUALIFICATION, phrase)
    a, c = (answer_range(b, "qualifications", s, s + len(phrase)) for s in (first, again))
    assert a.text == c.text and (a.start, c.start) == (first, again) and a != c


@pytest.mark.parametrize("change, message", [
    ({"start": 70, "end": 99}, "out of bounds"),
    ({"start": 5, "end": 5}, "out of bounds"),
    ({"start": 6, "end": 5}, "out of bounds"),
    ({"text": "Demand was steady."}, "text mismatch"),
    ({"field_sha256": "0" * 64}, "stale"),
    ({"documents_sha256": "0" * 64}, "stale"),
    ({"field_path": "question"}, "no answer field"),
    ({"field_path": "missing"}, "no answer field"),
])
def test_invalid_stale_and_out_of_bounds_ranges_fail(change, message):
    b = bundle()
    start = second(QUALIFICATION, "Demand was stable.")
    good = answer_range(b, "qualifications", start, start + 18).model_dump()
    with pytest.raises(ValueError, match=message):
        validate_answer_range(b, {**good, **change})


@pytest.mark.parametrize("change", [{"start": True}, {"start": 1.0}, {"start": "1"}, {"start": -1},
                                    {"offset_unit": "utf16"}, {"contract": "answer-annotation/v0"},
                                    {"text": ""}, {"field_sha256": "x"}, {"extra": 1}])
def test_malformed_ranges_are_refused_by_the_contract(change):
    b = bundle()
    good = answer_range(b, "qualifications", 0, 6).model_dump()
    with pytest.raises(ValidationError):
        AnswerRange.model_validate({**good, **change})


def test_range_recorded_against_changed_answer_text_is_stale():
    old, new = bundle(), bundle(QUALIFICATION.replace("stable", "steady"))
    r = answer_range(old, "qualifications", 0, 6)
    assert answer_range(new, "qualifications", 0, 6).text == r.text
    with pytest.raises(ValueError, match="stale"):
        validate_answer_range(new, r)
    with pytest.raises(ValueError, match="out of bounds"):
        answer_range(old, "qualifications", 0, len(QUALIFICATION) + 1)


# --- answers ------------------------------------------------------------------------------------------

def test_dispositions_drafts_and_completion_rules():
    b = bundle()
    draft = annotation(b, 0, 6)
    assert validate_answers(b, complete_answers([draft]))["annotations"][0]["disposition"] is None
    with pytest.raises(ValueError, match="disposition required"):
        validate_answers(b, complete_answers([draft]), complete=True)
    supported = annotation(b, 0, 6, disposition="supported")
    unverifiable = annotation(b, 9, 13, "ann:two", disposition="cannot_verify", reason="No source covers it.")
    saved = validate_answers(b, complete_answers([supported, unverifiable]), complete=True)
    assert [a["disposition"] for a in saved["annotations"]] == ["supported", "cannot_verify"]
    with pytest.raises(ValueError, match="reason required"):
        validate_answers(b, complete_answers([annotation(b, 0, 6, disposition="cannot_verify")]), complete=True)
    for kind in ("defective", "incomplete"):
        with pytest.raises(ValueError, match="materiality required"):
            validate_answers(b, complete_answers([annotation(b, 0, 6, disposition=kind, reason="x")]), complete=True)
        validate_answers(b, complete_answers([annotation(b, 0, 6, disposition=kind, reason="x", material=False)]),
                         complete=True)
    with pytest.raises(ValidationError, match="materiality applies only"):
        annotation(b, 0, 6, disposition="supported", material=True)
    with pytest.raises(ValueError, match="duplicate annotation"):
        validate_answers(b, complete_answers([supported, supported]))


def test_annotation_and_defect_ranges_and_source_selections_are_validated():
    b = bundle()
    source = b.sources[0]
    good = {"source_id": source.source_id, "source_hash": source.sha256, "start_line": 6, "end_line": 6,
            "excerpt": source.text.split("\n")[5]}
    a = annotation(b, 0, 6, disposition="supported", selections=[good])
    assert validate_answers(b, complete_answers([a]))["annotations"][0]["selections"][0]["start_line"] == 6
    bad = a.model_dump(mode="json")
    bad["selections"][0]["excerpt"] = "edited"
    with pytest.raises(ValueError, match="excerpt invalid"):
        validate_answers(b, {**complete_answers(), "annotations": [bad]})
    stale = annotation(b, 0, 6).model_dump(mode="json")
    stale["answer_range"]["end"] = 7
    with pytest.raises(ValueError, match="text mismatch"):
        validate_answers(b, {**complete_answers(), "annotations": [stale]})
    # An omission is a defect without any answer range; a bound defect names its exact passage.
    omission = Defect(defect_id="D1", category="omission", material=True, evidence_note="Omits the restatement.")
    bound = Defect(defect_id="D2", category="unsupported", material=False, evidence_note="x",
                   answer_ranges=[answer_range(b, "qualifications", 0, 6)])
    saved = validate_answers(b, complete_answers(defects=[omission, bound]), complete=True)
    assert "answer_ranges" not in saved["defects"][0] and saved["defects"][1]["answer_ranges"][0]["text"] == "Region"
    broken = bound.model_dump(mode="json")
    broken["answer_ranges"][0]["start"] = 99
    with pytest.raises(ValueError, match="out of bounds"):
        validate_answers(b, complete_answers(defects=[broken]))


def test_unknown_answer_sections_are_still_refused():
    with pytest.raises(ValueError, match="unknown answer section"):
        validate_answers(bundle(), {"judgments": {}, "defects": [], "notes": []})


# --- persistence, export and restore -------------------------------------------------------------------

def test_draft_restart_submit_amend_export_and_restore_preserve_annotations(tmp_path):
    b = bundle()
    original = canonical_json(b.model_dump(mode="json"))
    store = FileStore(tmp_path)
    store.register(b)
    first = annotation(b, 0, 6, disposition="supported")
    unverifiable = annotation(b, 9, 13, "ann:two", disposition="cannot_verify", reason="Not in any source.")
    store.save_snapshot(b, 0, "draft", complete_answers([first, unverifiable]), "Reviewer")
    restarted = FileStore(tmp_path)  # a fresh process reads the same durable state
    state = restarted.load_task(b.bundle_id)
    assert [a["annotation_id"] for a in state["answers"]["annotations"]] == ["ann:one", "ann:two"]
    restarted.save_submission(b, 1, "submit", state["answers"], "Reviewer")
    assert submission_matches_snapshot(restarted.load_task(b.bundle_id))
    amended = unverifiable.model_copy(update={"reason": "Checked again; still not in any source."})
    restarted.save_submission(b, 2, "amend", complete_answers([first, amended]), "Reviewer",
                              amendment_reason="clarified reason")
    one, three = (json.loads(export_json(restarted, b.bundle_id, r)) for r in (2, 3))
    assert one["annotations"][1]["reason"] == "Not in any source."
    assert three["annotations"][1]["reason"].startswith("Checked again")
    assert three["provenance"]["amends_revision"] == "2"
    assert three["annotations"][0]["annotation_id"] == "ann:one"
    # Restore: an exported record re-validates to the identical canonical bytes.
    restored = ReviewSubmission.model_validate(three)
    assert canonical_json(restored.model_dump(mode="json")) == export_json(restarted, b.bundle_id, 3)
    # Restoring a submission's answers as a new draft keeps every annotation.
    answers = {k: three[k] for k in ("judgments", "defects", "annotations")}
    assert restarted.save_snapshot(b, 3, "restore", answers, "Reviewer")["answers"]["annotations"] == three["annotations"]
    # Original report bytes and bundle identity are unchanged by annotating.
    assert canonical_json(b.model_dump(mode="json")) == original
    assert restarted.load_task(b.bundle_id)["bundle_hash"] == b.bundle_hash


def test_answers_without_annotations_keep_their_legacy_shape(tmp_path):
    b = bundle()
    cleaned = validate_answers(b, {"judgments": {}, "defects": [], "annotations": []})
    assert cleaned == {"judgments": {}, "defects": []}
    plain = Defect(defect_id="D1", category="c", material=None)
    assert "answer_ranges" not in plain.model_dump(mode="json")


def test_v041_submission_validates_and_exports_identical_bytes(tmp_path):
    fixture = ROOT / "tests/fixtures/diagnostic-free-v041"
    b = ReviewBundle.model_validate(json.loads((fixture / "bundle.json").read_text()))
    assert b.bundle_hash == "9dde844428f8f6114e9b8cb088aefa123f79e1ea1c637b228f2ab82f26092052"
    shutil.copytree(fixture / "store", tmp_path / "store")
    store = FileStore(tmp_path / "store")
    raw = store.load_task(b.bundle_id)["submissions"]["1"]
    assert "annotations" not in raw and all("answer_ranges" not in d for d in raw["defects"])
    exported = export_json(store, b.bundle_id, 1)
    assert exported == canonical_json(raw) and digest(json.loads(exported)) == digest(raw)
    assert submission_matches_snapshot(store.load_task(b.bundle_id))
    # An old client that never sends annotations still saves and resumes unchanged.
    state = store.load_task(b.bundle_id)
    after = store.save_snapshot(b, state["revision"], "legacy-client", state["answers"], state["assessor"])
    assert "annotations" not in after["answers"] and export_json(store, b.bundle_id, 1) == exported


# --- published contract ---------------------------------------------------------------------------------

def test_published_schemas_and_capabilities_name_the_contract():
    for folder in (ROOT / "schemas", ROOT / "src/evidence_review/schemas"):
        assert json.loads((folder / "answer-annotation-v1.json").read_text()) == AnswerAnnotation.model_json_schema()
        assert json.loads((folder / "review-submission-v1.json").read_text()) == ReviewSubmission.model_json_schema()
    assert "answer-annotation/v1" in capabilities()["contracts"]
    schema = AnswerAnnotation.model_json_schema()
    assert schema["properties"]["disposition"]["anyOf"][0]["enum"] == ["supported", "defective", "cannot_verify",
                                                                      "incomplete"]
    # Nothing in the contract can assert that a whole answer was reviewed.
    assert not {"coverage", "complete", "reviewed_all"} & set(schema["properties"])
