"""Pinned reviewer/candidate arithmetic and sealed worksheet replay (I12178).

The evaluator owns its Decimal context: a caller's precision, rounding, exponent
limits, capitalisation or cleared traps change nothing it computes. Export replays
a sealed worksheet and keeps its stored computation byte for byte, or refuses it
naming the worksheet; it never recomputes a different historical result.
"""
import decimal
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_review import evidence, export_json
from evidence_review.contracts import (SEALED, Calculation, CalculationWorksheet, ReviewBundle, ReviewSubmission,
                                       canonical_json, digest, finite_decimal, sealed_submission)
from evidence_review.evidence import ARITHMETIC_CONTEXT, ARITHMETIC_SEMANTICS, calculate, worksheet_computation
from evidence_review.store import FileStore
from test_reviewer_worksheet import answers, revenue_worksheet, worksheet_bundle

FIXTURES = Path(__file__).parent / "fixtures"
# Hostile caller contexts: each would change a result computed in the ambient context.
HOSTILE = {
    "prec6": dict(prec=6),
    "round-down": dict(rounding=decimal.ROUND_DOWN),
    "traps-cleared": dict(traps=[]),
    "prec6-round-down-traps-cleared": dict(prec=6, rounding=decimal.ROUND_DOWN, traps=[]),
    "lowercase-small-exponents": dict(capitals=0, Emax=99, Emin=-99, prec=40, rounding=decimal.ROUND_CEILING),
}
CONTEXTS = [pytest.param({}, id="default"), *(pytest.param(v, id=k) for k, v in HOSTILE.items())]


def test_semantics_id_names_exactly_the_pinned_context():
    c = ARITHMETIC_CONTEXT
    traps = "+".join(s.__name__ for s in (decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow)
                     if c.traps[s])
    assert {s.__name__ for s, on in c.traps.items() if on} == set(traps.split("+"))
    rounding = c.rounding
    assert ARITHMETIC_SEMANTICS == (f"decimal-v1:prec={c.prec},rounding={rounding},Emin={c.Emin},Emax={c.Emax},"
                                    f"capitals={c.capitals},clamp={c.clamp},traps={traps},finite")
    # v1 equals CPython's default context, so values sealed under defaults replay digit for digit.
    d = decimal.DefaultContext
    assert (c.prec, c.rounding, c.Emin, c.Emax, c.capitals, c.clamp) == (d.prec, d.rounding, d.Emin, d.Emax,
                                                                          d.capitals, d.clamp)
    assert {s for s, on in c.traps.items() if on} == {s for s, on in d.traps.items() if on}


@pytest.mark.parametrize("ctx", CONTEXTS)
def test_evaluator_ignores_the_callers_context_and_restores_it(ctx):
    with decimal.localcontext(**ctx) as caller:
        before = (caller.prec, caller.rounding, caller.capitals, dict(caller.traps))
        assert calculate("a / b", {"a": "1", "b": "3"}, "0.333", "0.001") == {
            "status": "match", "result": "0.3333333333333333333333333333",
            "discrepancy": "0.0003333333333333333333333333", "evidence_verified": False}
        assert calculate("a * b", {"a": "1e60", "b": "1e60"}, "0", "1e200")["result"] == "1E+120"
        assert calculate("a / b", {"a": "1", "b": "0"}, "1", "0")["reason"] == "[<class 'decimal.DivisionByZero'>]"
        assert calculate("a / b", {"a": "0", "b": "0"}, "1", "0")["status"] == "unresolved"
        w = CalculationWorksheet.model_validate(revenue_worksheet(worksheet_bundle(), reported_value="-4.16667",
                                                                  tolerance="0"))
        assert w.computation["result"] == "-4.166666666666666666666666667"
        assert w.computation["comparison"] == "mismatch" and w.computation["arithmetic"] == ARITHMETIC_SEMANTICS
        # Parse errors are input errors whatever traps the caller cleared, never "non-finite".
        assert finite_decimal("abc", "x") is False
        assert (caller.prec, caller.rounding, caller.capitals, dict(caller.traps)) == before
        assert not caller.flags[decimal.Inexact]  # the evaluator's flags never leak into the caller


def _comp(formula, **ops):
    return CalculationWorksheet(worksheet_id="w", formula=formula,
                                operands=[{"name": k, "value": v} for k, v in ops.items()]).computation


@pytest.mark.parametrize("ctx", CONTEXTS)
@pytest.mark.parametrize("formula,ops,reason", [
    ("a / b", {"a": "1", "b": "0"}, "division by zero"),
    ("a / b", {"a": "0", "b": "0"}, "division by zero"),
    ("a / (b - b)", {"a": "-2", "b": "3"}, "division by zero"),
    ("a * a * a", {"a": "9e400000"}, "result outside the decimal range"),
    ("a * 10", {"a": "9e999999"}, "result outside the decimal range"),
    ("a", {"a": "9e1000000"}, "result outside the decimal range"),
    ("a + 1e1000000", {"a": "1"}, "result outside the decimal range"),
])
def test_faults_stay_explicit_calculation_errors_under_any_caller_context(ctx, formula, ops, reason):
    with decimal.localcontext(**ctx):
        c = _comp(formula, **ops)
    assert (c["status"], c["reason"], c["result"], c["comparison"]) == ("calculation_error", reason, None,
                                                                        "not_compared")
    assert c["arithmetic"] == ARITHMETIC_SEMANTICS


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
def test_a_non_finite_evaluator_result_is_never_a_result(monkeypatch, value):
    # Independent of traps: even an evaluator that returned a non-finite value is refused.
    monkeypatch.setattr(evidence, "evaluate", lambda formula, values: decimal.Decimal(value))
    c = _comp("a", a="1")
    assert (c["status"], c["reason"], c["result"]) == ("calculation_error", "non-finite result", None)


@pytest.mark.parametrize("ctx", CONTEXTS)
def test_candidate_recomputation_and_bundle_identity_are_context_independent(ctx):
    calc = dict(formula="a / b", result="0.3333", tolerance="0.0001",
                operands=[{"name": "a", "value": "1", "kind": "constant"}, {"name": "b", "value": "3", "kind": "constant"}])
    with decimal.localcontext():
        expected = Calculation.model_validate(calc).recomputation
        hashes = {"worksheet": worksheet_bundle().bundle_hash}
    with decimal.localcontext(**ctx):
        assert canonical_json(Calculation.model_validate(calc).recomputation) == canonical_json(expected)
        assert worksheet_bundle().bundle_hash == hashes["worksheet"]
        for name in ("diagnostic-free-v041", "worksheet-v060"):
            fixture = FIXTURES / name
            bundle = ReviewBundle.model_validate(json.loads((fixture / "bundle.json").read_text()))
            assert bundle.bundle_hash == json.loads((fixture / "provenance.json").read_text())["bundle_hash"]


def _submit(store, b, ctx):
    rev = revenue_worksheet(b, reported_value="-4.16667", tolerance="0")
    zero = {**rev, "worksheet_id": "W-zero", "operands": [rev["operands"][0], {**rev["operands"][1], "value": "0"}]}
    big = {**rev, "worksheet_id": "W-big", "formula": "current * prior * 1e999999"}
    with decimal.localcontext(**ctx):
        return store.save_submission(b, 0, "submit", answers(b, rev, zero, big), "Ada", 1)


@pytest.mark.parametrize("save_ctx", CONTEXTS)
def test_save_restart_export_under_differing_contexts_preserves_the_sealed_worksheet(tmp_path, save_ctx):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    state = _submit(store, b, save_ctx)
    sealed = state["submissions"]["1"]
    by_id = {w["worksheet_id"]: w["computation"] for w in sealed["worksheets"]}
    assert by_id["W-revenue"]["result"] == "-4.166666666666666666666666667"
    assert by_id["W-revenue"]["comparison"] == "mismatch"
    assert by_id["W-zero"]["reason"] == "division by zero"
    assert by_id["W-big"]["reason"] == "result outside the decimal range"
    assert all(c["arithmetic"] == ARITHMETIC_SEMANTICS for c in by_id.values())
    exports = set()
    for export_ctx in [{}, *HOSTILE.values()]:
        with decimal.localcontext(**export_ctx):
            restarted = FileStore(tmp_path)  # restart: a fresh store replays the durable record
            exports.add(export_json(restarted, b.bundle_id, 1))
            assert restarted.export_submission(b.bundle_id, 1).model_dump(mode="json") == sealed
    assert exports == {canonical_json(sealed)}


@pytest.mark.parametrize("name", ["diagnostic-free-v041", "worksheet-v060"])
@pytest.mark.parametrize("ctx", CONTEXTS)
def test_historical_submissions_export_byte_identical_under_any_context(tmp_path, name, ctx):
    fixture = FIXTURES / name
    shutil.copytree(fixture / "store", tmp_path / "store")
    raw = json.loads((tmp_path / "store/synthetic/snapshot.json").read_text())["submissions"]["1"]
    with decimal.localcontext(**ctx):
        out = export_json(FileStore(tmp_path / "store"), "synthetic", 1)
    assert out == canonical_json(raw)
    if name == "worksheet-v060":
        assert digest(out.decode()) == json.loads((fixture / "provenance.json").read_text())["export_sha256"]
        # 0.6.0 records carry no semantics id and are never given one on export.
        assert all("arithmetic" not in w["computation"] for w in json.loads(out)["worksheets"])
        assert {w["worksheet_id"]: w["computation"]["status"] for w in raw["worksheets"]} == {
            "W-revenue": "computed", "W-zero": "calculation_error", "W-third": "computed",
            "W-unavailable": "incomplete"}


def test_supplied_computations_are_never_trusted_on_new_writes(tmp_path):
    b = worksheet_bundle()
    forged = {"evaluator": "evidence_review.evidence.evaluate", "arithmetic": ARITHMETIC_SEMANTICS,
              "status": "computed", "result": "999", "comparison": "match", "discrepancy": "0", "reason": "",
              "unavailable": [], "evidence_verified": True, "note": ""}
    for supplied in (forged, {k: v for k, v in forged.items() if k != "arithmetic"},
                     {**forged, "arithmetic": "decimal-v9:prec=2"}):
        w = CalculationWorksheet.model_validate({**revenue_worksheet(b), "computation": supplied})
        assert w.computation == worksheet_computation(w) and w.computation["result"].startswith("-4.1666")
        assert w.computation["evidence_verified"] is False and w.computation["arithmetic"] == ARITHMETIC_SEMANTICS
    store = FileStore(tmp_path)
    store.register(b)
    state = store.save_submission(b, 0, "k", answers(b, {**revenue_worksheet(b), "computation": forged}), "Ada")
    kept = state["submissions"]["1"]["worksheets"][0]["computation"]
    assert kept["result"].startswith("-4.1666") and kept["evidence_verified"] is False
    # A submission assembled by a caller is a new write too: only export replays sealed records.
    sub = dict(state["submissions"]["1"], worksheets=[{**state["submissions"]["1"]["worksheets"][0],
                                                       "computation": forged}])
    assert ReviewSubmission.model_validate(sub).worksheets[0].computation == kept
    with pytest.raises(ValidationError, match="worksheet W-revenue: sealed computation does not match"):
        sealed_submission(sub)


def _tamper(root, task, change):
    """Edit the durable record in place (events log and snapshot), as an attacker or bad migration would."""
    events = root / task / "events.jsonl"
    lines = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
    change(lines[-1]["state"]["submissions"]["1"]["worksheets"][0]["computation"])
    events.write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in lines))
    snapshot = root / task / "snapshot.json"
    state = json.loads(snapshot.read_text())
    change(state["submissions"]["1"]["worksheets"][0]["computation"])
    snapshot.write_text(json.dumps(state))


@pytest.mark.parametrize("change,message", [
    (lambda c: c.update(result="-4.17"), "sealed computation does not match its deterministic replay"),
    (lambda c: c.update(comparison="mismatch"), "sealed computation does not match its deterministic replay"),
    (lambda c: c.update(evidence_verified=0), "sealed computation does not match its deterministic replay"),
    (lambda c: c.pop("note"), "sealed computation does not match its deterministic replay"),
    (lambda c: c.update(arithmetic="decimal-v2:prec=34"), "unknown arithmetic semantics"),
    (lambda c: c.update(arithmetic=None), "unknown arithmetic semantics"),
])
def test_a_tampered_sealed_computation_is_refused_on_export_never_rewritten(tmp_path, change, message):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    store.save_submission(b, 0, "k", answers(b, revenue_worksheet(b)), "Ada")
    _tamper(tmp_path, b.bundle_id, change)
    before = {p.name: p.read_bytes() for p in (tmp_path / b.bundle_id).iterdir() if p.is_file()}
    for ctx in [{}, HOSTILE["prec6-round-down-traps-cleared"]]:
        with decimal.localcontext(**ctx), pytest.raises(ValidationError, match="worksheet W-revenue: " + message):
            FileStore(tmp_path).export_submission(b.bundle_id, 1)
    after = {p.name: p.read_bytes() for p in (tmp_path / b.bundle_id).iterdir() if p.is_file()}
    assert after == before


def test_a_legacy_record_whose_replay_disagrees_is_refused_not_upgraded(tmp_path):
    # A 0.6.0 worksheet computed under a non-default ambient context: replay under v1 disagrees.
    shutil.copytree(FIXTURES / "worksheet-v060/store", tmp_path / "store")
    _tamper(tmp_path / "store", "synthetic", lambda c: c.update(result="-4.16667", discrepancy="0.03333"))
    with pytest.raises(ValidationError, match="worksheet W-revenue: sealed computation does not match"):
        FileStore(tmp_path / "store").export_submission("synthetic", 1)


def test_sealed_flag_is_only_set_by_the_replay_path():
    assert SEALED == "evidence_review.sealed"
    b = worksheet_bundle()
    w = CalculationWorksheet.model_validate(revenue_worksheet(b))
    data = w.model_dump(mode="json")
    replayed = CalculationWorksheet.model_validate(data, context={SEALED: True})
    assert replayed.computation is not None and canonical_json(replayed.computation) == canonical_json(w.computation)
