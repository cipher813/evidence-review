"""Reviewer-authored calculation worksheets: safe recomputation, durable and separate."""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_review import export_json, open_review
from evidence_review.contracts import (Calculation, CalculationWorksheet, Claim, FormField, Operand,
                                       ReviewBundle, ReviewSubmission, canonical_json, digest)
from evidence_review.evidence import calculate, passage
from evidence_review.store import FileStore, submission_matches_snapshot
from synthetic import build, located, source
from test_server import request

CHOICES = ["supported", "unsupported", "cannot determine"]


def worksheet_bundle():
    """A deliberately wrong supplied formula (margin) and a claim with no formula (revenue)."""
    current, prior = located(6, "24.6%"), located(6, "22.8%")
    margin = Claim(claim_id="margin", text="Operating margin rose 180 bps to 24.6%.", citations=[prior, current],
                   calculation=Calculation(formula="(current - prior) * 10", result="180", unit="bps", tolerance="0.01",
                                           operands=[Operand(name="current", value="24.6", unit="pct", period="FY2026", citation=current),
                                                     Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=prior)]))
    revenue = Claim(claim_id="revenue", text="Revenue fell 4.2%.", citations=[located(7, "1,150")])
    b = build(
        [("summary", "Operating margin rose 180 bps to 24.6%. Revenue fell 4.2%.", ["margin", "revenue"])],
        [margin, revenue],
        {("summary", "180 bps"): ("derived", ["margin"]), ("summary", "24.6%"): ("cited", ["margin"]),
         ("summary", "4.2%"): ("cited", ["revenue"])},
        form=[FormField(field_id="support:revenue", label="Revenue support", options=CHOICES, subject_id="revenue"),
              FormField(field_id="report_complete", label="I reviewed the full report.", kind="boolean", require_true=True)],
    )
    for n in b.spans:
        if n.text == "180 bps":
            n.calculation = margin.calculation
        elif n.text == "24.6%":
            n.citations = [current]
    return b.model_validate(b.model_dump(mode="json"))


def selection(b, line):
    s = b.sources[0]
    return {"source_id": s.source_id, "source_hash": s.sha256, "start_line": line, "end_line": line,
            "excerpt": passage(s, line, line)["text"]}


def revenue_worksheet(b, **extra):
    return {"worksheet_id": "W-revenue", "subject": {"kind": "claim", "id": "revenue"},
            "formula": "(current - prior) / prior * 100",
            "operands": [{"name": "current", "value": "1150", "unit": "$m", "period": "FY2026", "entity": "Synthetic issuer",
                          "metric": "revenue", "selections": [selection(b, 7), selection(b, 4)]},
                         {"name": "prior", "value": "1200", "unit": "$m", "period": "FY2025", "entity": "Synthetic issuer",
                          "metric": "revenue", "selections": [selection(b, 7)]}],
            "reported_value": "-4.2", "unit": "pct", "tolerance": "0.05",
            "rationale": "Recomputed the decline from the table rows.", **extra}


def answers(b, *worksheets):
    out = {"judgments": {"support:revenue": {"value": "supported"}, "report_complete": {"value": True}}, "defects": []}
    if worksheets:
        out["worksheets"] = list(worksheets)
    return out


def test_computation_is_derived_reviewer_labelled_and_never_infers_support():
    b = worksheet_bundle()
    w = CalculationWorksheet.model_validate({**revenue_worksheet(b), "computation": {"status": "computed", "result": "1"},
                                             "reviewer_support": "unknown"})
    assert w.authorship == "reviewer" and w.contract == "reviewer-calculation-worksheet/v1"
    c = w.computation
    assert c["status"] == "computed" and c["comparison"] == "match" and c["evidence_verified"] is False
    assert c["result"].startswith("-4.16666") and c["evaluator"] == "evidence_review.evidence.evaluate"
    # Arithmetic agreement leaves the reviewer's support judgment exactly as entered.
    assert w.reviewer_support == "unknown"
    with pytest.raises(ValidationError):
        CalculationWorksheet.model_validate({**revenue_worksheet(b), "authorship": "candidate"})


@pytest.mark.parametrize("formula", ["__import__('os').system('true')", "a ** 2", "a.real", "a if a else 1",
                                     "'1' + a", "True + a", "a < 1", "[a][0]", "1j * a", "a @ a"])
def test_unsafe_expressions_are_refused(formula):
    with pytest.raises(ValidationError, match="unsupported formula syntax"):
        CalculationWorksheet(worksheet_id="w", formula=formula, operands=[{"name": "a", "value": "1"}])


@pytest.mark.parametrize("field", ["value", "reported", "tolerance"])
@pytest.mark.parametrize("text", ["NaN", "Infinity", "-inf", "sNaN"])
def test_non_finite_inputs_are_refused(field, text):
    data = {"worksheet_id": "w", "formula": "a", "operands": [{"name": "a", "value": "1"}]}
    if field == "value":
        data["operands"][0]["value"] = text
    else:
        data["reported_value" if field == "reported" else "tolerance"] = text
    with pytest.raises(ValidationError, match="non-finite"):
        CalculationWorksheet.model_validate(data)


def test_errors_and_unavailable_inputs_are_retained_explicitly():
    def comp(formula, **ops):
        operands = [{"name": k, **({"availability": "unavailable"} if v is None else {"value": v})} for k, v in ops.items()]
        return CalculationWorksheet(worksheet_id="w", formula=formula, operands=operands).computation

    zero = comp("a / b", a="1", b="0")
    assert (zero["status"], zero["reason"], zero["result"]) == ("calculation_error", "division by zero", None)
    assert comp("a / (b - b)", a="0", b="3")["reason"] == "division by zero"
    assert comp("a * a * a", a="9e400000")["reason"] == "result outside the decimal range"
    missing = comp("a + b", a="1", b=None)
    assert missing["status"] == "incomplete" and missing["unavailable"] == ["b"] and missing["result"] is None
    assert comp("", a="1")["reason"] == "no formula entered"
    assert comp("a +", a="1")["status"] == "invalid"
    assert comp("a + c", a="1")["reason"] == "undeclared operand: c"
    assert comp("a", a="1,2")["reason"] == "operand value is not a decimal number: a"
    assert comp("a + 0.5", a="1")["comparison"] == "not_compared"
    with pytest.raises(ValidationError, match="unavailable operand carries no value"):
        CalculationWorksheet(worksheet_id="w", operands=[{"name": "a", "value": "1", "availability": "unavailable"}])
    with pytest.raises(ValidationError, match="duplicate worksheet operand"):
        CalculationWorksheet(worksheet_id="w", operands=[{"name": "a"}, {"name": "a"}])
    with pytest.raises(ValidationError, match="reserved word"):
        CalculationWorksheet(worksheet_id="w", operands=[{"name": "lambda"}])


def test_supplied_formula_recomputation_is_unchanged_by_the_shared_evaluator():
    # The refactored evaluator returns the exact dictionaries sealed bundles carry.
    assert calculate("(a - b) * 100", {"a": "24.6", "b": "22.8"}, "180", "0.01") == {
        "status": "match", "result": "180.0", "discrepancy": "0.0", "evidence_verified": False}
    assert calculate("a / b", {"a": "1", "b": "0"}, "1", "0")["reason"] == "[<class 'decimal.DivisionByZero'>]"
    assert calculate("a ** 2", {"a": "1"}, "1", "0")["reason"] == "unsupported formula syntax"
    assert calculate("x" * 1001, {}, "1", "0")["reason"] == "formula too long"


def test_store_saves_exports_and_keeps_the_original_formula(tmp_path):
    b = worksheet_bundle()
    original = b.model_dump(mode="json")
    store = FileStore(tmp_path)
    store.register(b)
    margin = {"worksheet_id": "W-margin", "subject": {"kind": "span", "id": "summary:22:29"}, "formula": "(current - prior) * 100",
              "operands": [{"name": "current", "value": "24.6", "unit": "pct", "period": "FY2026", "selections": [selection(b, 6)]},
                           {"name": "prior", "value": "22.8", "unit": "pct", "period": "FY2025", "selections": [selection(b, 6)]}],
              "reported_value": "180", "unit": "bps", "tolerance": "0.01", "rationale": "The supplied formula scales by 10, not 100."}
    unavailable = {"worksheet_id": "W-unavailable", "subject": {"kind": "claim", "id": "revenue"}, "formula": "segment / total * 100",
                   "operands": [{"name": "segment", "availability": "unavailable", "unavailable_reason": "No segment table in the sources."},
                                {"name": "total", "value": "1150", "selections": [selection(b, 7)]}],
                   "rationale": "Share cannot be recomputed without the segment value."}
    state = store.save_submission(b, 0, "submit", answers(b, revenue_worksheet(b), margin, unavailable), "Ada", 3)
    sub = store.export_submission(b.bundle_id, state["revision"])
    by_id = {w.worksheet_id: w for w in sub.worksheets}
    assert by_id["W-margin"].computation["comparison"] == "match"
    assert by_id["W-unavailable"].computation["status"] == "incomplete"
    assert by_id["W-unavailable"].reviewer_support == "unknown"
    assert len(by_id["W-revenue"].operands[0].selections) == 2
    # The supplied candidate formula is untouched and still mismatches; the bundle never changes.
    assert b.claims[0].calculation.formula == "(current - prior) * 10"
    assert b.claims[0].calculation.recomputation["arithmetic"]["status"] == "mismatch"
    assert b.model_dump(mode="json") == original
    exported = json.loads(export_json(store, b.bundle_id, state["revision"]))
    assert exported["worksheets"][1]["formula"] == "(current - prior) * 100"
    assert exported["worksheets"][1]["authorship"] == "reviewer"
    assert ReviewSubmission.model_validate(exported).model_dump(mode="json") == exported
    assert submission_matches_snapshot(store.load_task(b.bundle_id))
    # Revision: amending changes the worksheet, the first revision is preserved exactly.
    changed = answers(b, {**revenue_worksheet(b), "tolerance": "0.001"})
    amended = store.save_submission(b, 1, "amend", changed, "Ada", 1, "Tighter tolerance")
    assert store.export_submission(b.bundle_id, 1).model_dump(mode="json") == exported
    assert store.export_submission(b.bundle_id, amended["revision"]).worksheets[0].computation["comparison"] == "mismatch"
    # Restart: a fresh store replays the same durable record.
    assert FileStore(tmp_path).export_submission(b.bundle_id, 1).model_dump(mode="json") == exported


def test_subjects_are_validated(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    defect = {"defect_id": "D1", "category": "arithmetic", "material": True, "evidence_note": "Wrong scale."}
    ok = answers(b, revenue_worksheet(b, subject={"kind": "defect", "id": "D1"}))
    ok["defects"] = [defect]
    store.save_snapshot(b, 0, "defect-link", ok, "Ada")
    for kind, ident in [("claim", "nope"), ("span", "summary:0:1"), ("defect", "D2"), ("reference", "r"),
                        ("annotation", "ann-1")]:
        with pytest.raises(ValueError, match="unknown worksheet subject"):
            store.save_snapshot(b, 1, "bad-" + kind, answers(b, revenue_worksheet(b, subject={"kind": kind, "id": ident})), "Ada")
    with pytest.raises(ValidationError):
        store.save_snapshot(b, 1, "bad-kind", answers(b, revenue_worksheet(b, subject={"kind": "field", "id": "x"})), "Ada")
    with pytest.raises(ValueError, match="duplicate worksheet identity"):
        store.save_snapshot(b, 1, "dup", answers(b, revenue_worksheet(b), revenue_worksheet(b)), "Ada")


def test_drafts_retain_partial_work_but_submission_requires_a_whole_worksheet(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    draft = {"worksheet_id": "W1", "formula": "a /", "operands": [{"name": "a", "value": "-"}]}
    saved = store.save_snapshot(b, 0, "draft", answers(b, draft), "Ada")
    assert saved["answers"]["worksheets"][0]["computation"]["status"] == "invalid"
    full = revenue_worksheet(b)
    cases = [({"subject": None}, "subject required"), ({"formula": "current +"}, "syntax"),
             ({"rationale": " "}, "rationale required"), ({"reported_value": "abc"}, "reported value"),
             ({"tolerance": "-1"}, "tolerance"),
             ({"operands": [{**full["operands"][0], "selections": []}, full["operands"][1]]}, "source passage"),
             ({"operands": [full["operands"][0], {"name": "prior", "availability": "unavailable"}]}, "reason required")]
    for i, (change, message) in enumerate(cases):
        with pytest.raises(ValueError, match=message):
            store.save_submission(b, 1, f"s{i}", answers(b, {**full, **change}), "Ada")
    bad = {**full, "operands": [{**full["operands"][0], "selections": [{**selection(b, 7), "excerpt": "forged"}]}, full["operands"][1]]}
    with pytest.raises(ValueError, match="source selection excerpt invalid"):
        store.save_snapshot(b, 1, "forged", answers(b, bad), "Ada")
    zero = {**full, "operands": [full["operands"][0], {**full["operands"][1], "value": "0"}]}
    state = store.save_submission(b, 1, "zero", answers(b, zero), "Ada")
    kept = store.export_submission(b.bundle_id, state["revision"]).worksheets[0]
    assert kept.computation["status"] == "calculation_error" and kept.computation["reason"] == "division by zero"


def test_answers_without_worksheets_keep_their_exact_shape_and_hash(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    state = store.save_submission(b, 0, "plain", {**answers(b), "worksheets": []}, "Ada")
    assert "worksheets" not in state["answers"] and "worksheets" not in state["submissions"]["1"]
    sub = store.export_submission(b.bundle_id, 1)
    assert "worksheets" not in sub.model_dump(mode="json")
    assert "worksheets" not in json.loads(export_json(store, b.bundle_id, 1))


def test_historical_store_and_submission_identity_are_unchanged(tmp_path):
    fixture = Path(__file__).parent / "fixtures/diagnostic-free-v041"
    bundle = ReviewBundle.model_validate(json.loads((fixture / "bundle.json").read_text()))
    shutil.copytree(fixture / "store", tmp_path / "store")
    raw = json.loads((tmp_path / "store/synthetic/snapshot.json").read_text())["submissions"]["1"]
    store = FileStore(tmp_path / "store")
    exported = store.export_submission(bundle.bundle_id, 1).model_dump(mode="json")
    assert exported == raw and digest(exported) == digest(raw)
    assert canonical_json(exported) == canonical_json(raw)
    assert bundle.bundle_hash == json.loads((fixture / "provenance.json").read_text())["bundle_hash"]


def test_server_round_trip_recomputes_on_save(tmp_path):
    b = worksheet_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        body = {"bundle_id": b.bundle_id, "bundle_hash": b.bundle_hash, "revision": 0, "key": "k1",
                "answers": answers(b, {**revenue_worksheet(b), "computation": {"status": "computed", "result": "999"}}),
                "assessor": "Ada"}
        state = json.load(request(h, "/api/save", "POST", body, Origin=h.origin))
        assert state["answers"]["worksheets"][0]["computation"]["result"].startswith("-4.1666")
        body.update(revision=1, key="k2", answers=answers(b, {**revenue_worksheet(b), "formula": "open('x')"}))
        from urllib.error import HTTPError
        with pytest.raises(HTTPError) as error:
            request(h, "/api/save", "POST", body, Origin=h.origin)
        assert error.value.code == 400 and "unsupported formula syntax" in json.load(error.value)["error"]
