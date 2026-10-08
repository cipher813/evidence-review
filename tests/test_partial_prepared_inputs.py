"""Audit regression (alpha-engine-config-I12167, partial prepared operands); every value is synthetic.

A number's candidate calculation may leave an input unresolved while an
independently located copy of that input is frozen in the span's
``prepared_inputs``. The atomic row shows every such operand beside the
original input, which keeps its own status, and never checks the row.
"""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import atom_view, build_atom_manifest, citation_target_id, validate_atom_evidence
from evidence_review.contracts import Calculation, Claim, FormField, Operand, validate_bundle
from synthetic import build, located, margin_claim, unavailable

ATOMIC = "atomic-source-check/v1"


def partial_margin(text="Margin rose 180 bps.", nested=False, check=False):
    """The auditor's input: prior is unresolved in the original calculation; 22.8% is located independently."""
    b = build([("summary", text, ["c"])], [Claim(claim_id="c", text=text)])
    d = b.model_dump(mode="json")
    original = margin_claim(prior_citation=unavailable()).calculation.model_dump(mode="json")
    for s in d["spans"]:
        s.update(state="derived", claim_ids=["c"], calculation=original)
        if nested:
            parts = Calculation(formula="a + b", operands=[
                Operand(name="a", value="22.0", unit="pct", period="FY2025", citation=located(6, "22.8%")),
                Operand(name="b", value="0.8", unit="pct", period="FY2025", citation=located(6, "22.8%"))],
                result="22.8", unit="pct")
            prior = Operand(name="prior", value="22.8", unit="pct", period="FY2025", kind="derived", calculation=parts)
        else:
            prior = Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=located(6, "22.8%"))
        s["prepared_inputs"] = [prior.model_dump(mode="json")]
    if check:
        d["form"].append(FormField(field_id="quantity:margin", kind="boolean", required=False, label="Checked",
                                   subject_id="c", numeric_span_id=d["spans"][0]["span_id"]).model_dump(mode="json"))
    return validate_bundle(d)


def rows_of(b, facts=()):
    m = validate_atom_evidence(b, build_atom_manifest(b, list(facts)))
    return m, atom_view(b, m)["rows"]


def with_item(manifest, index=0, **changes):
    d = manifest.model_dump(mode="json")
    d["items"][index].update(changes)
    return d


def test_direct_partial_input_is_shown_beside_the_unresolved_original():
    b = partial_margin()
    span = b.spans[0]
    m, [r] = rows_of(b)
    assert r["prepared_input_refs"] == [f"prepared-input:{span.span_id}:0"] == m.items[0].prepared_input_refs
    # The original candidate input is unchanged: its located sibling and its unresolved prior both remain.
    assert [(x["status"], x["target"] is None) for x in r["calculation_leaves"]] == [("located", False),
                                                                                     ("unavailable", True)]
    [o] = r["prepared_inputs"]
    assert (o["name"], o["value"], o["unit"], o["period"]) == ("prior", "22.8", "pct", "FY2025")
    assert o["original"]["status"] == "unavailable"
    assert o["source"]["status"] == "located" and o["source"]["start_line"] == 6
    assert o["source"]["target"]["target_id"] == citation_target_id(b, located(6, "22.8%"))
    assert o["source"]["target"]["binding"] == "prepared_input"
    assert "never checks a row" in o["provenance"] and "Not a support judgment" in o["provenance"]
    assert r["preparations"] == [] and r["evidence_state"] == "derived"


def test_nested_partial_input_exposes_every_part_and_the_original_failure_stays_explicit():
    b = partial_margin(nested=True)
    _, [r] = rows_of(b)
    [o] = r["prepared_inputs"]
    assert o["source"] is None and o["calculation"]["formula"] == "a + b"
    assert [(x["status"], x["start_line"], x["target"]["binding"]) for x in o["calculation_leaves"]] == [
        ("located", 6, "prepared_input"), ("located", 6, "prepared_input")]
    # The original candidate's unresolved prior is still shown, next to its located sibling.
    assert [x["status"] for x in r["calculation_leaves"]] == ["located", "unavailable"]
    assert o["original"]["status"] == "unavailable"


def test_wrong_occurrence_omitted_unknown_and_duplicate_input_references_are_refused():
    b = partial_margin(text="Margin rose 180 bps and 180 bps.")
    m = build_atom_manifest(b)
    first, second = (i.prepared_input_refs[0] for i in m.items)
    assert first != second and validate_atom_evidence(b, m)
    for change, message in [
        ({"prepared_input_refs": [second]}, "belongs to another occurrence"),
        ({"prepared_input_refs": []}, "never hidden"),
        ({"prepared_input_refs": [first, first]}, "duplicate prepared input reference"),
        ({"prepared_input_refs": [first.rsplit(":", 1)[0] + ":1"]}, "unknown prepared input"),
        ({"prepared_input_refs": [first.rsplit(":", 1)[0] + ":00"]}, "unknown prepared input"),
        ({"prepared_input_refs": [first.replace("prepared-input:", "prepared:")]}, "unknown prepared input"),
    ]:
        with pytest.raises(ValueError, match=message):
            validate_atom_evidence(b, with_item(m, **change))


def test_fact_may_show_inputs_of_a_number_inside_its_own_words_only():
    b = partial_margin(text="Margin rose 180 bps and 180 bps.")
    text = b.fields[0].text
    first, second = b.spans
    inner = {"field_path": "summary", "start": 0, "end": text.index(" and"), "claim_ids": ["c"],
             "reason": "No source declared", "prepared_input_refs": [f"prepared-input:{first.span_id}:0"]}
    _, rows = rows_of(b, [inner])
    fact = next(r for r in rows if r["kind"] == "fact")
    assert [o["ref"] for o in fact["prepared_inputs"]] == inner["prepared_input_refs"]
    with pytest.raises(ValueError, match="belongs to another occurrence"):
        validate_atom_evidence(b, build_atom_manifest(b, [{**inner, "prepared_input_refs": [
            f"prepared-input:{second.span_id}:0"]}]))


def test_rows_without_prepared_inputs_serialize_and_identify_as_before():
    b = partial_margin()
    d = b.model_dump(mode="json")
    d["spans"][0]["prepared_inputs"] = []
    plain = validate_bundle(d)
    m, [r] = rows_of(plain)
    assert r["prepared_inputs"] == [] and r["prepared_input_refs"] == []
    assert "prepared_input_refs" not in json.loads(m.model_dump_json())["items"][0]
    # The atom's identity does not depend on its prepared inputs.
    assert m.items[0].atom_id == build_atom_manifest(b).items[0].atom_id


def test_browser_shows_and_opens_the_partial_input_without_checking(tmp_path):
    b = partial_margin(check=True)
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, presentation_mode=ATOMIC) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        row = page.locator("[data-atom-row]").first
        row.locator("summary").first.click()
        box = row.get_by_role("region", name="Independently located inputs for “180 bps”")
        expect(box).to_contain_text("Independently located input “prior”: 22.8 pct · FY2025")
        expect(box).to_contain_text("Original candidate input: unavailable")
        box.get_by_role("button", name="Open the located source for input “prior” of “180 bps”").click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("22.8%")
        expect(row.locator("input[type=checkbox]")).not_to_be_checked()
        assert store.load_task(b.bundle_id)["answers"]["judgments"].get("quantity:margin") is None
        assert not errors
        browser.close()
