"""0.5.5 display context: operand metric, the author's declared tolerance and support declaration.

Every value is synthetic. All three fields are optional and display only: they are
shown beside the evidence, they never change recomputation, a row's evidence state,
a check control or the verified-verdict gate, and an absent value keeps the durable
identity of bundles sealed before the field existed.
"""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import atom_view, build_atom_manifest, validate_atom_evidence
from evidence_review.contracts import Calculation, Claim, FormField, Operand, ReviewBundle, validate_bundle
from evidence_review.store import validate_answers
from rendered_fixtures import html_bundle, html_sidecars
from synthetic import build, located, margin_claim, unavailable

ATOMIC = "atomic-source-check/v1"
DECLARATION = "inference: derived from the margin trend"
PROSE_TOLERANCE = "0.1 percentage point"


def margin_calculation(metric=True, declared=""):
    current, prior = located(6, "24.6%"), located(6, "22.8%")
    return Calculation(formula="(current - prior) * 100", operands=[
        Operand(name="current", value="24.6", unit="pct", period="FY2026", entity="Synthetic issuer",
                metric="operating margin" if metric else "", citation=current),
        Operand(name="prior", value="22.8", unit="pct", period="FY2025", entity="Synthetic issuer",
                metric="operating margin" if metric else "", citation=prior)],
        result="180", unit="bps", tolerance="0.01", declared_tolerance=declared)


def margin_bundle(metric=True, declared="", declaration="", check=False, text="Margin rose 180 bps."):
    calc = margin_calculation(metric, declared)
    claim = Claim(claim_id="margin", text=text, citations=[located(6, "24.6%")], calculation=calc,
                  support_declaration=declaration)
    b = build([("summary", text, ["margin"])], [claim])
    d = b.model_dump(mode="json")
    for s in d["spans"]:
        s.update(state="derived", claim_ids=["margin"], calculation=calc.model_dump(mode="json"))
    if check:
        d["form"].append(FormField(field_id="verdict:margin", label="Source verdict", options=["verified", "incorrect"],
                                   required=True, subject_id="margin", numeric_verification_values=["verified"]
                                   ).model_dump(mode="json"))
        d["form"].append(FormField(field_id="quantity:margin", kind="boolean", required=False, label="Checked",
                                   subject_id="margin", numeric_span_id=d["spans"][0]["span_id"]
                                   ).model_dump(mode="json"))
    return validate_bundle(d)


def rows_of(b):
    return atom_view(b, validate_atom_evidence(b, build_atom_manifest(b)))["rows"]


# --- schema ---------------------------------------------------------------------------------------------

def test_operand_round_trips_with_and_without_metric():
    with_metric = Operand(name="prior", value="22.8", unit="pct", period="FY2025", entity="Synthetic issuer",
                          metric="operating margin", citation=located(6, "22.8%"))
    dumped = with_metric.model_dump(mode="json")
    assert dumped["metric"] == "operating margin"
    assert Operand.model_validate(dumped) == with_metric
    plain = Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=located(6, "22.8%"))
    assert "metric" not in plain.model_dump(mode="json")
    assert Operand.model_validate(plain.model_dump(mode="json")).metric == ""


def test_calculation_and_claim_round_trip_with_and_without_declarations():
    calc = margin_calculation(declared=PROSE_TOLERANCE)
    assert Calculation.model_validate(calc.model_dump(mode="json")).declared_tolerance == PROSE_TOLERANCE
    assert "declared_tolerance" not in margin_calculation().model_dump(mode="json")
    claim = Claim(claim_id="c", text="t", support_declaration=DECLARATION)
    assert Claim.model_validate(claim.model_dump(mode="json")).support_declaration == DECLARATION
    assert "support_declaration" not in Claim(claim_id="c", text="t").model_dump(mode="json")


def test_whole_bundle_round_trips_and_supplied_values_are_hashed():
    full = margin_bundle(declared=PROSE_TOLERANCE, declaration=DECLARATION)
    again = validate_bundle(full.model_dump(mode="json"))
    assert again == full and again.bundle_hash == full.bundle_hash
    # A supplied value is part of the durable identity, like every other field.
    assert margin_bundle(metric=False).bundle_hash != margin_bundle().bundle_hash
    assert margin_bundle(declared=PROSE_TOLERANCE).bundle_hash != margin_bundle().bundle_hash
    assert margin_bundle(declaration=DECLARATION).bundle_hash != margin_bundle().bundle_hash


def test_absent_fields_keep_the_identity_of_bundles_sealed_before_them():
    b = margin_bundle(metric=False)
    payload = b.model_dump(mode="json")
    # The pre-0.5.5 serialization carried none of the three keys, so the same bytes are hashed.
    operands = [o for c in payload["claims"] for o in c["calculation"]["operands"]] + [
        o for s in payload["spans"] for o in s["calculation"]["operands"]]
    assert operands and all("metric" not in o for o in operands)
    assert "declared_tolerance" not in repr(payload) and "support_declaration" not in repr(payload)
    assert validate_bundle(payload).bundle_hash == b.bundle_hash
    assert ReviewBundle.model_validate(payload).bundle_hash == b.bundle_hash


def test_declared_tolerance_is_never_parsed_and_never_used_in_recomputation():
    plain = margin_calculation()
    for prose in (PROSE_TOLERANCE, "1000", "±50 bps", "not a number"):
        declared = margin_calculation(declared=prose)
        assert declared.recomputation == plain.recomputation
        assert declared.tolerance == "0.01"
    # A result outside the numeric tolerance stays a mismatch whatever the prose says.
    off = Calculation(formula="(current - prior) * 100", operands=plain.operands, result="181", unit="bps",
                      tolerance="0.01", declared_tolerance="5 bps")
    assert off.recomputation["status"] == Calculation(
        formula="(current - prior) * 100", operands=plain.operands, result="181", unit="bps",
        tolerance="0.01").recomputation["status"] != "match"


def test_tolerance_stays_a_nonnegative_decimal():
    calc = Calculation(formula="a", operands=[Operand(name="a", value="1", citation=located(6, "24.6%"))],
                       result="1", tolerance="-0.01", declared_tolerance="any amount")
    assert calc.recomputation["status"] == "unresolved"


# --- atom_view ------------------------------------------------------------------------------------------

def test_atom_row_carries_operand_metric_and_declared_tolerance():
    [r] = rows_of(margin_bundle(declared=PROSE_TOLERANCE))
    assert [o["metric"] for o in r["calculation"]["operands"]] == ["operating margin", "operating margin"]
    assert [o["entity"] for o in r["calculation"]["operands"]] == ["Synthetic issuer", "Synthetic issuer"]
    assert r["calculation"]["declared_tolerance"] == PROSE_TOLERANCE and r["calculation"]["tolerance"] == "0.01"


def test_prepared_input_row_carries_its_metric():
    b = margin_bundle()
    d = b.model_dump(mode="json")
    original = margin_claim(prior_citation=unavailable()).calculation.model_dump(mode="json")
    d["spans"][0]["calculation"] = original
    d["spans"][0]["prepared_inputs"] = [Operand(
        name="prior", value="22.8", unit="pct", period="FY2025", entity="Synthetic issuer",
        metric="operating margin", citation=located(6, "22.8%")).model_dump(mode="json")]
    [r] = rows_of(validate_bundle(d))
    [o] = r["prepared_inputs"]
    assert (o["metric"], o["entity"], o["period"], o["unit"]) == ("operating margin", "Synthetic issuer",
                                                                    "FY2025", "pct")


def test_support_declaration_is_shown_beside_the_row_and_changes_no_status():
    plain, declared = rows_of(margin_bundle()), rows_of(margin_bundle(declaration=DECLARATION))
    [d] = declared[0]["author_declarations"]
    assert d["claim_id"] == "margin" and d["declaration"] == DECLARATION and d["label"] == "Author's declaration"
    assert "not a citation" in d["provenance"] and "never checks a row" in d["provenance"]
    assert plain[0]["author_declarations"] == []
    # Nothing else about the row moves: state, reason, targets, candidates, leaves and bindings are identical.
    # (The bundle hash differs because a supplied declaration is hashed; that is identity, not status.)
    strip = [{k: v for k, v in r.items() if k not in ("author_declarations", "bundle_hash")}
             for r in (plain[0], declared[0])]
    assert strip[0] == strip[1]


def test_unsupported_declaration_does_not_make_a_located_row_unresolved_or_vice_versa():
    unsupported = rows_of(margin_bundle(declaration="unsupported: no filing covers FY2027"))[0]
    assert unsupported["evidence_state"] == rows_of(margin_bundle())[0]["evidence_state"]
    b = build([("summary", "Margin rose 180 bps.", ["c"])],
              [Claim(claim_id="c", text="Margin rose 180 bps.", support_declaration="supported: see the filing")])
    [r] = rows_of(b)
    assert r["targets"] == [] and r["evidence_state"] == rows_of(build(
        [("summary", "Margin rose 180 bps.", ["c"])], [Claim(claim_id="c", text="Margin rose 180 bps.")]))[0][
        "evidence_state"]


def test_support_declaration_never_satisfies_the_verified_verdict_gate():
    for declaration in ("", DECLARATION, "supported: verified against the filing"):
        b = margin_bundle(declaration=declaration, check=True)
        answers = {"judgments": {"verdict:margin": {"value": "verified"}, "report_complete": {"value": True}},
                   "defects": []}
        with pytest.raises(ValueError, match="unchecked quantity or atom"):
            validate_answers(b, answers, complete=True)
        answers["judgments"]["quantity:margin"] = {"value": True}
        validate_answers(b, answers, complete=True)


# --- Chromium -------------------------------------------------------------------------------------------

def declared_html_bundle():
    """The rendered-source fixture with metric, prose tolerance and a declaration on its one claim."""
    b = html_bundle()
    d = b.model_dump(mode="json")
    claim = d["claims"][0]
    claim["support_declaration"] = DECLARATION
    for calc in [claim["calculation"], *(s["calculation"] for s in d["spans"] if s["calculation"])]:
        calc["declared_tolerance"] = PROSE_TOLERANCE
        for o in calc["operands"]:
            o.update(metric="operating margin", entity="Synthetic issuer")
    return validate_bundle(d)


def test_browser_expanded_row_shows_metric_tolerance_and_declaration_without_checking(tmp_path):
    b = declared_html_bundle()
    atoms, asset = html_sidecars(b)
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        row = page.locator("[data-atom-row]").filter(has_text="“180 bps”")
        check = row.locator("input[type=checkbox]")
        row.locator("summary").first.click()
        more = row.locator(".atom-expand")
        expect(more.locator(".input-derivation").first).to_contain_text("24.6 pct · operating margin · FY2026")
        expect(more.locator(".input-derivation").nth(1)).to_contain_text("22.8 pct · operating margin · FY2025")
        assert more.locator(".input-derivation a, .input-derivation span").first.get_attribute("title") == \
            "Synthetic issuer · operating margin"
        tolerance = more.locator(".calc-tolerance").first
        expect(tolerance).to_be_visible()
        expect(tolerance).to_contain_text("Tolerance ±0.01 bps")
        expect(tolerance).to_contain_text(f"author's declared tolerance: {PROSE_TOLERANCE}")
        declaration = more.get_by_role("complementary", name="Author's declaration for statement margin")
        expect(declaration).to_contain_text(f"Author's declaration: {DECLARATION}")
        expect(declaration).to_contain_text("not a support judgment")
        # Expanding, reading and opening never check; the state label is the evidence state, not the declaration.
        expect(check).not_to_be_checked()
        expect(row.locator(".atom-state")).not_to_contain_text("inference")
        expect(page.locator("#status")).to_contain_text("revision 0")
        assert store.load_task(b.bundle_id)["answers"]["judgments"] == {}
        assert not errors
        browser.close()


def test_browser_prepared_input_shows_its_metric(tmp_path):
    b = margin_bundle(check=True)
    d = b.model_dump(mode="json")
    d["spans"][0]["calculation"] = margin_claim(prior_citation=unavailable()).calculation.model_dump(mode="json")
    d["spans"][0]["prepared_inputs"] = [Operand(
        name="prior", value="22.8", unit="pct", period="FY2025", entity="Synthetic issuer",
        metric="operating margin", citation=located(6, "22.8%")).model_dump(mode="json")]
    b = validate_bundle(d)
    with open_review(b, FileStore(tmp_path), launch=False, presentation_mode=ATOMIC) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        row = page.locator("[data-atom-row]").first
        row.locator("summary").first.click()
        box = row.get_by_role("region", name="Independently located inputs for “180 bps”")
        expect(box).to_contain_text(
            "Independently located input “prior”: 22.8 pct · operating margin · FY2025 · Synthetic issuer")
        expect(row.locator(".calc-tolerance").first).to_contain_text("Tolerance ±0.01 bps")
        expect(row.locator("input[type=checkbox]")).not_to_be_checked()
        browser.close()


def test_browser_evidence_pane_shows_the_declaration_as_the_authors_not_as_support(tmp_path):
    b = margin_bundle(declaration=DECLARATION, declared=PROSE_TOLERANCE)
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_role("region", name="Report", exact=True).get_by_role(
            "button", name="180 bps, derived", exact=True).click()
        card = page.get_by_role("region", name="Calculation details")
        expect(card.locator(".calc-tolerance")).to_contain_text("Tolerance ±0.01 bps")
        expect(card).to_contain_text("operating margin")
        expect(card.get_by_role("complementary", name="Author's declaration for statement margin")).to_contain_text(
            f"Author's declaration: {DECLARATION}")
        card.get_by_role("button", name="Preview calculation evidence").click()
        pane = page.get_by_role("region", name="Evidence")
        expect(pane.locator(".calc-tolerance")).to_contain_text(f"Tolerance ±0.01 bps · author's declared tolerance: "
                                                                f"{PROSE_TOLERANCE}")
        expect(pane.get_by_role("complementary", name="Author's declaration for statement margin")).to_contain_text(
            "not a support judgment")
        pane.get_by_text("Original candidate calculation").first.click()
        expect(pane).to_contain_text("Absolute tolerance: 0.01 bps")
        expect(pane).to_contain_text(
            f"Author's declared tolerance (display only; not used in recomputation): {PROSE_TOLERANCE}")
        # The whole-claim view shows it too, once.
        page.get_by_text("All claims and reference items").click()
        page.locator("#subjects").get_by_role("button", name="Margin rose 180 bps.").click()
        expect(pane.locator(".author-declaration")).to_have_count(1)
        expect(pane.locator(".author-declaration")).to_contain_text(DECLARATION)
        assert not errors
        browser.close()
