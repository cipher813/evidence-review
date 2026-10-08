"""Audit regressions (evidence-review-I31, I32); every value is synthetic.

I31: an assigned atom row and the frozen check control bound to its occurrence name each other, both ways.
I32: a number's occurrence-bound prepared calculation is shown in its row, beside the original candidate
evidence and never in its place.
"""
import json

import pytest

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import atom_view, build_atom_manifest, citation_target_id, validate_atom_evidence
from evidence_review.contracts import (Calculation, Claim, FormField, NumericDiagnostics, Operand, PreparationAttempt,
                                       PreparedEvidence, TechnicalDiagnostic, validate_bundle)
from rendered_fixtures import html_bundle, outlook_fact
from synthetic import build, located, margin_claim, source, unavailable

ATOMIC = "atomic-source-check/v1"


def checked_revenue():
    """The auditor's I31 input: one cited quantity, its bound check and a support verdict that needs it."""
    b = build([("summary", "Revenue was 10 units.", ["c"])],
              [Claim(claim_id="c", text="Revenue was 10 units.", citations=[located(1, "10"), located(2, "99")])],
              sources=[source("Revenue was 10 units.\nHeadcount was 99 people.\n")])
    d = b.model_dump(mode="json")
    d["spans"][0].update(state="cited", claim_ids=["c"], citations=[located(1, "10").model_dump(mode="json")])
    d["form"].append(FormField(field_id="quantity:check", kind="boolean", required=False, label="Checked 10",
                               subject_id="c", numeric_span_id=d["spans"][0]["span_id"]).model_dump(mode="json"))
    d["form"].append(FormField(field_id="support:c", subject_id="c", label="Support",
                               options=["supported", "cannot_verify"],
                               numeric_verification_values=["supported"]).model_dump(mode="json"))
    return validate_bundle(d)


def with_item(manifest, index=0, **changes):
    d = manifest.model_dump(mode="json")
    d["items"][index].update(changes)
    return d


def unchecked_bundle():
    """The fixture with no row-bound controls at all: every atom is legitimately unassigned."""
    d = html_bundle().model_dump(mode="json")
    d["form"] = [{k: v for k, v in f.items() if k != "numeric_verification_values"} for f in d["form"]
                 if not f.get("numeric_span_id") and not f.get("atom")]
    return validate_bundle(d)


# ---------------------------------------------------------------- I31: quantity rows


def test_quantity_row_carries_its_frozen_check_and_omitting_it_is_refused():
    b = checked_revenue()
    m = build_atom_manifest(b)
    assert validate_atom_evidence(b, m).items[0].form_field_id == "quantity:check"
    assert atom_view(b, m)["rows"][0]["form_field_id"] == "quantity:check"
    with pytest.raises(ValueError, match="omits its frozen check control 'quantity:check'"):
        validate_atom_evidence(b, with_item(m, form_field_id=None))
    del (d := m.model_dump(mode="json"))["items"][0]["form_field_id"]  # Absent, not just null.
    with pytest.raises(ValueError, match="omits its frozen check control"):
        validate_atom_evidence(b, d)


@pytest.mark.parametrize("wrong,message", [
    ("quantity:missing", "which the frozen form does not have"),
    ("support:c", "binds 'quantity:check' to it"),
    ("report_complete", "binds 'quantity:check' to it"),
])
def test_quantity_row_naming_another_or_unknown_control_is_refused(wrong, message):
    b = checked_revenue()
    with pytest.raises(ValueError, match=message):
        validate_atom_evidence(b, with_item(build_atom_manifest(b), form_field_id=wrong))


def test_quantity_row_naming_another_numbers_check_is_refused_both_ways():
    b = html_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    rows = {i.text: i for i in m.items}
    index = [i.text for i in m.items].index("24.6%")
    other = rows["1,150"].form_field_id
    with pytest.raises(ValueError, match=f"names check control '{other}', but the frozen form binds"):
        validate_atom_evidence(b, with_item(m, index, form_field_id=other))


def test_unassigned_quantity_without_a_bound_check_stays_valid_and_cannot_claim_one():
    b = unchecked_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    manifest = validate_atom_evidence(b, m)
    assert all(i.form_field_id is None for i in manifest.items)
    assert all(r["form_field_id"] is None for r in atom_view(b, manifest)["rows"])
    with pytest.raises(ValueError, match="does not bind to this atom"):
        validate_atom_evidence(b, with_item(m, form_field_id="report_complete"))
    with pytest.raises(ValueError, match="which the frozen form does not have"):
        validate_atom_evidence(b, with_item(m, form_field_id="quantity:invented"))


# ---------------------------------------------------------------- I31: fact rows


def fact_index(manifest):
    return next(k for k, i in enumerate(manifest.items) if i.kind == "fact")


def test_fact_row_carries_its_frozen_check_and_omitting_or_replacing_it_is_refused():
    b = html_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    k = fact_index(m)
    assert validate_atom_evidence(b, m).items[k].form_field_id == "fact:outlook"
    with pytest.raises(ValueError, match="fact atom .* omits its frozen check control 'fact:outlook'"):
        validate_atom_evidence(b, with_item(m, k, form_field_id=None))
    quantity_check = next(f.field_id for f in b.form if f.numeric_span_id)
    for wrong, message in ((quantity_check, "binds 'fact:outlook' to it"),
                           ("fact:invented", "which the frozen form does not have")):
        with pytest.raises(ValueError, match=message):
            validate_atom_evidence(b, with_item(m, k, form_field_id=wrong))


def test_unassigned_fact_without_a_bound_check_stays_valid_and_cannot_claim_one():
    b = unchecked_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    k = fact_index(m)
    assert validate_atom_evidence(b, m).items[k].form_field_id is None
    with pytest.raises(ValueError, match="does not bind to this atom"):
        validate_atom_evidence(b, with_item(m, k, form_field_id="verdict:margin"))


def test_malformed_sidecar_is_refused_before_serving(tmp_path):
    b = checked_revenue()
    dropped = with_item(build_atom_manifest(b), form_field_id=None)
    with pytest.raises(ValueError, match="omits its frozen check control"):
        with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=dropped, presentation_mode=ATOMIC):
            pass  # pragma: no cover - never reached
    assert not list(tmp_path.rglob("*.json"))  # Nothing was registered or served.


def test_required_check_stays_reachable_and_gates_support_submission(tmp_path):
    b = checked_revenue()
    m = validate_atom_evidence(b, build_atom_manifest(b))
    field = m.items[0].form_field_id
    store = FileStore(tmp_path)
    store.register(b)
    answers = {"judgments": {"report_complete": {"value": True}, "support:c": {"value": "supported"}}, "defects": []}
    with pytest.raises(ValueError):
        store.save_submission(b, 0, "missing-check", answers, "Auditor", 1)
    answers["judgments"][field] = {"value": True}
    assert store.save_submission(b, 0, "with-check", answers, "Auditor", 1)["revision"] == 1


# ---------------------------------------------------------------- I32: prepared calculations


def prepared_margin(original=None, prepared=None, diagnostics=None, claim_ids=()):
    """The auditor's I32 input: "Margin rose 180 bps." with a prepared calculation on the number."""
    b = build([("summary", "Margin rose 180 bps.", ["c"])], [Claim(claim_id="c", text="Margin rose 180 bps.")])
    d = b.model_dump(mode="json")
    span = d["spans"][0]
    span["claim_ids"] = list(claim_ids)
    if original is not None:
        span.update(state="derived", claim_ids=["c"], calculation=original.model_dump(mode="json"))
    span["prepared_evidence"] = [p.model_dump(mode="json") for p in (prepared if prepared is not None else [
        PreparedEvidence(calculation=margin_claim().calculation, reason="Independent complete calculation")])]
    if diagnostics is not None:
        span["diagnostics"] = diagnostics.model_dump(mode="json")
    return validate_bundle(d)


def row_of(b, manifest=None, facts=()):
    manifest = validate_atom_evidence(b, manifest or build_atom_manifest(b, list(facts)))
    return manifest, atom_view(b, manifest)["rows"]


def test_preparation_only_row_shows_the_prepared_calculation_and_keeps_the_original_empty():
    b = prepared_margin()
    span = b.spans[0]
    manifest, rows = row_of(b)
    r = rows[0]
    # Original candidate evidence is unchanged: nothing was supplied by the answer.
    assert (r["evidence_state"], r["calculation"], r["calculation_leaves"], r["targets"]) == ("unavailable", None, [], [])
    assert r["calculation_ref"] is None and r["form_field_id"] is None
    assert r["prepared_refs"] == [f"prepared:{span.span_id}:0"] == manifest.items[0].prepared_refs
    [p] = r["preparations"]
    assert p["ref"] == f"prepared:{span.span_id}:0" and p["label"] == "Independently prepared calculation"
    assert p["origin"] == "independently_located" and p["reason"] == "Independent complete calculation"
    assert "not a support judgment" in p["provenance"] and "never checks a row" in p["provenance"]
    assert p["calculation"]["result"] == "180" and p["calculation"]["recomputation"]["status"] == "match"
    leaves = p["calculation_leaves"]
    assert [(x["start_line"], x["status"]) for x in leaves] == [(6, "located"), (6, "located")]
    assert all(x["target"]["binding"] == "prepared_calculation_input" for x in leaves)
    assert {x["target"]["target_id"] for x in leaves} == {citation_target_id(b, located(6, "24.6%")),
                                                          citation_target_id(b, located(6, "22.8%"))}


def test_original_only_row_has_no_preparation_and_serializes_as_before():
    b = html_bundle()
    manifest, rows = row_of(b, facts=[outlook_fact(b)])
    assert all(r["preparations"] == [] and r["prepared_refs"] == [] for r in rows)
    calc = next(r for r in rows if r["text"] == "180 bps")
    assert calc["evidence_state"] == "derived" and calc["calculation"]["result"] == "180"
    assert all("prepared_refs" not in i for i in json.loads(manifest.model_dump_json())["items"])


def test_original_and_conflicting_preparation_stay_separate():
    conflicting = Calculation(formula="(current - prior) * 100", operands=margin_claim().calculation.operands,
                              result="170", unit="bps", tolerance="0.01")
    b = prepared_margin(original=margin_claim().calculation,
                        prepared=[PreparedEvidence(calculation=conflicting, reason="Second method"),
                                  PreparedEvidence(citations=[located(6, "24.6%")], reason="Direct line")])
    span = b.spans[0]
    _, [r] = row_of(b)
    assert r["evidence_state"] == "derived" and r["calculation_ref"] == f"span:{span.span_id}"
    assert r["calculation"]["result"] == "180" and r["calculation"]["recomputation"]["status"] == "match"
    assert [x["target"]["binding"] for x in r["calculation_leaves"]] == ["calculation_input"] * 2
    first, second = r["preparations"]
    assert first["label"] == "Independently prepared calculation 1 of 2"
    assert first["calculation"]["result"] == "170" and first["calculation"]["recomputation"]["status"] != "match"
    assert second["label"] == "Independently prepared evidence 2 of 2" and second["calculation"] is None
    assert [t["binding"] for t in second["targets"]] == ["prepared_evidence"]
    assert r["targets"] == []  # A prepared direct citation is shown as preparation, never promoted to a target.


def test_unavailable_preparation_is_retained_without_inventing_evidence():
    attempt = PreparationAttempt(method="synthetic-locator", status="unresolved", diagnostics=[
        TechnicalDiagnostic(scope="preparation", outcome="unresolved", reason="No independent source found")])
    b = prepared_margin(prepared=[], diagnostics=NumericDiagnostics(preparation_status="unresolved",
                                                                    attempts=[attempt]))
    _, [r] = row_of(b)
    assert (r["evidence_state"], r["preparations"], r["prepared_refs"]) == ("unavailable", [], [])
    # A preparation whose input could not be located stays visible with that limitation.
    partial = Calculation(formula="(current - prior) * 100", operands=[
        Operand(name="current", value="24.6", unit="pct", period="FY2026", citation=located(6, "24.6%")),
        Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=unavailable())],
        result="180", unit="bps", tolerance="0.01")
    b = prepared_margin(prepared=[PreparedEvidence(calculation=partial, reason="One input missing")])
    _, [r] = row_of(b)
    [p] = r["preparations"]
    assert p["calculation"]["recomputation"]["status"] == "unresolved"
    assert [(x["status"], x["target"] is None) for x in p["calculation_leaves"]] == [("located", False),
                                                                                      ("unavailable", True)]


def test_nested_prepared_operands_expose_every_leaf():
    nested = Calculation(formula="(current - prior) * 100", operands=[
        Operand(name="current", value="24.6", unit="pct", period="FY2026", kind="derived",
                calculation=Calculation(formula="a + b", operands=[
                    Operand(name="a", value="22.8", unit="pct", citation=located(6, "22.8%")),
                    Operand(name="b", value="1.8", unit="pct", kind="constant")], result="24.6", unit="pct")),
        Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=located(6, "22.8%"))],
        result="180", unit="bps", tolerance="0.01")
    b = prepared_margin(prepared=[PreparedEvidence(calculation=nested, reason="Nested subtotal")])
    _, [r] = row_of(b)
    [p] = r["preparations"]
    assert p["calculation"]["operands"][0]["calculation"]["formula"] == "a + b"
    assert [(x["start_line"], x["target"]["binding"]) for x in p["calculation_leaves"]] == [
        (6, "prepared_calculation_input"), (6, "prepared_calculation_input")]


def two_prepared_numbers():
    text = "Margin rose 180 bps and 180 bps."
    b = build([("summary", text, ["c"])], [Claim(claim_id="c", text=text)])
    d = b.model_dump(mode="json")
    for s in d["spans"]:
        s["prepared_evidence"] = [PreparedEvidence(calculation=margin_claim().calculation,
                                                   reason="Prepared " + s["span_id"]).model_dump(mode="json")]
    return validate_bundle(d)


def test_wrong_occurrence_omitted_unknown_and_substituted_prepared_references_are_refused():
    b = two_prepared_numbers()
    m = build_atom_manifest(b)
    first, second = (i.prepared_refs[0] for i in m.items)
    assert first != second and validate_atom_evidence(b, m)
    cases = [
        ({"prepared_refs": [second]}, "belongs to another occurrence"),
        ({"prepared_refs": []}, "never hidden"),
        ({"prepared_refs": [first, first]}, "duplicate prepared evidence reference"),
        ({"prepared_refs": [first.rsplit(":", 1)[0] + ":1"]}, "unknown prepared evidence"),
        ({"prepared_refs": [first.rsplit(":", 1)[0] + ":00"]}, "unknown prepared evidence"),
        ({"prepared_refs": ["prepared:summary:99:101:0"]}, "unknown prepared evidence"),
        # Preparation never replaces the original candidate calculation.
        ({"evidence_state": "derived", "calculation_ref": first}, "never replaces an atom's original calculation_ref"),
    ]
    for change, message in cases:
        with pytest.raises(ValueError, match=message):
            validate_atom_evidence(b, with_item(m, **change))


def test_fact_may_show_preparation_of_a_number_inside_its_own_words_only():
    b = two_prepared_numbers()
    text = b.fields[0].text
    first_span, second_span = b.spans
    inner = {"field_path": "summary", "start": 0, "end": text.index(" and"), "claim_ids": ["c"],
             "reason": "No source declared for this fact", "prepared_refs": [f"prepared:{first_span.span_id}:0"]}
    manifest, rows = row_of(b, facts=[inner])
    fact = next(r for r in rows if r["kind"] == "fact")
    assert [p["ref"] for p in fact["preparations"]] == inner["prepared_refs"]
    outside = {**inner, "prepared_refs": [f"prepared:{second_span.span_id}:0"]}
    with pytest.raises(ValueError, match="belongs to another occurrence"):
        validate_atom_evidence(b, build_atom_manifest(b, [outside]))


def test_preparation_leaves_the_check_control_unanswered():
    b = prepared_margin(claim_ids=["c"])
    d = b.model_dump(mode="json")
    d["form"].append(dict(field_id="quantity:margin", label="Checked 180 bps", kind="boolean", required=False,
                          subject_id="c", numeric_span_id=b.spans[0].span_id))
    b = validate_bundle(d)
    _, [r] = row_of(b)
    assert r["form_field_id"] == "quantity:margin" and r["preparations"]
