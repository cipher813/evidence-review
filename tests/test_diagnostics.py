"""Scoped diagnostics preserve evidence and leave judgments to the caller."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from evidence_review.contracts import ReviewBundle
from test_browser_review import margin_bundle, launch
from evidence_review import FileStore, open_review
from playwright.sync_api import sync_playwright, expect


def diagnostics():
    return {"preparation_status": "resolved", "candidate": [
        {"scope":"candidate_input", "outcome":"inconsistent", "reason":"Original excerpt does not match <img src=x onerror=alert(1)>",
         "input_path":["subtotal","prior"], "citation":{"source_id":"filing","start_line":6,"end_line":6,"excerpt":"absent","status":"excerpt_mismatch","reason":"excerpt absent from range"}},
        {"scope":"candidate_arithmetic","outcome":"inconsistent","reason":"Recomputed 170; reported 180"}
    ], "attempts":[
        {"method":"exact table cell","status":"unresolved","diagnostics":[{"scope":"preparation","outcome":"unresolved","reason":"Period context is ambiguous"}]},
        {"method":"cited input rows","status":"resolved","diagnostics":[]}
    ]}


def diagnostic_bundle():
    b=margin_bundle()
    data=b.model_dump(mode="json")
    s=next(s for s in data["spans"] if s["text"]=="180 bps")
    s["diagnostics"]=diagnostics()
    s["prepared_evidence"]=[{"calculation":deepcopy(s["calculation"]), "reason":"Prepared exact input rows"}]
    return ReviewBundle.model_validate(data)


def test_diagnostics_round_trip_and_old_bundle_compatibility():
    b=diagnostic_bundle()
    s=next(s for s in b.spans if s.text=="180 bps")
    assert s.diagnostics.candidate[0].input_path==["subtotal","prior"]
    assert s.diagnostics.candidate[0].citation.status=="excerpt_mismatch"
    assert ReviewBundle.model_validate(b.model_dump()).bundle_hash==b.bundle_hash
    old=margin_bundle().model_dump()
    for s in old["spans"]: s.pop("diagnostics",None)
    assert ReviewBundle.model_validate(old).spans[0].diagnostics is None


@pytest.mark.parametrize("change",["outcome","missing_reason","missing_failure","conflicting_prepared"])
def test_contradictory_or_unattributed_preparation_is_rejected(change):
    data=diagnostic_bundle().model_dump()
    s=next(s for s in data["spans"] if s["text"]=="180 bps")
    if change=="outcome": s["diagnostics"]["preparation_status"]="unresolved"
    if change=="missing_reason": s["diagnostics"]["candidate"][0]["reason"]=""
    if change=="missing_failure": s["diagnostics"]["attempts"][0]["diagnostics"]=[]
    if change=="conflicting_prepared": s["prepared_evidence"]=[]
    with pytest.raises(ValidationError): ReviewBundle.model_validate(data)


def test_scoped_diagnostics_inline_expanded_safe_text_and_unchanged_answers(tmp_path):
    b=diagnostic_bundle(); store=FileStore(tmp_path)
    with open_review(b,store,launch=False) as h,sync_playwright() as pw:
        browser=launch(pw); page=browser.new_page(); page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        before=store.load_task(b.bundle_id)["answers"]
        page.get_by_role("region",name="Report",exact=True).get_by_role("button",name="180 bps, candidate evidence derived; preparation resolved",exact=True).click()
        card=page.get_by_role("region",name="Calculation details")
        expect(card).to_contain_text("Independent preparation: resolved")
        expect(card).to_contain_text("Earlier method — exact table cell: unresolved")
        expect(card).to_contain_text("Original excerpt does not match <img")
        expect(card).to_contain_text("subtotal → prior")
        assert card.locator("img").count()==0
        texts=card.locator(".number-diagnostics").inner_text()
        page.get_by_role("button",name="Preview calculation evidence",exact=True).click()
        panel=page.get_by_role("region",name="Evidence")
        assert panel.locator(".number-diagnostics").inner_text()==texts
        panel.get_by_role("button",name="Inspect diagnostic source filing",exact=True).click()
        expect(panel).to_contain_text("Operating margin")
        assert store.load_task(b.bundle_id)["answers"]==before
        browser.close()

@pytest.mark.parametrize("change", ["attempt_scope", "candidate_scope", "resolved_failure", "identifier"])
def test_diagnostic_scope_and_success_cannot_misrepresent_an_attempt(change):
    data=diagnostic_bundle().model_dump()
    s=next(s for s in data["spans"] if s["text"]=="180 bps")
    d=s["diagnostics"]
    if change=="attempt_scope": d["attempts"][0]["diagnostics"][0]["scope"]="candidate_input"
    if change=="candidate_scope": d["candidate"][0]["scope"]="preparation"
    if change=="resolved_failure": d["attempts"][1]["diagnostics"]=deepcopy(d["attempts"][0]["diagnostics"])
    if change=="identifier":
        s["prepared_evidence"]=[];s["calculation"]=None;s["state"]="identifier"
        d["preparation_status"]="unresolved";d["attempts"]=d["attempts"][:1]
    with pytest.raises(ValidationError): ReviewBundle.model_validate(data)


def test_unresolved_diagnostics_remain_visible_with_safe_bad_range_inspection(tmp_path):
    data=diagnostic_bundle().model_dump()
    s=next(s for s in data["spans"] if s["text"]=="180 bps")
    s["prepared_evidence"]=[];s["diagnostics"]["preparation_status"]="unresolved"
    s["diagnostics"]["attempts"]=s["diagnostics"]["attempts"][:1]
    s["diagnostics"]["candidate"][0]["citation"].update(start_line=999,end_line=1000,status="invalid_locator",reason="outside frozen source")
    b=ReviewBundle.model_validate(data)
    with open_review(b,FileStore(tmp_path),launch=False) as h,sync_playwright() as pw:
        browser=launch(pw);page=browser.new_page();page.goto(h.url)
        page.get_by_role("region",name="Report",exact=True).get_by_role("button",name="180 bps, candidate evidence derived; preparation unresolved",exact=True).click()
        card=page.get_by_role("region",name="Calculation details")
        expect(card).to_contain_text("Independent preparation: unresolved")
        expect(card).not_to_contain_text("Earlier method")
        card.get_by_role("button",name="Inspect diagnostic source filing",exact=True).click()
        expect(page.get_by_role("region",name="Evidence")).to_contain_text("Synthetic filing")
        browser.close()


def test_diagnostics_participate_in_blinding(tmp_path):
    from evidence_review.contracts import blind_violations
    b=diagnostic_bundle()
    assert blind_violations(b.model_dump(),["original excerpt"])
    with pytest.raises(ValueError, match="blind"):
        open_review(b,FileStore(tmp_path),launch=False,blind_markers=["Original excerpt"])


def test_v041_store_reopens_with_original_identity_answers_and_submission(tmp_path):
    import json
    import shutil
    from pathlib import Path
    fixture=Path(__file__).parent/"fixtures/diagnostic-free-v041"
    payload=json.loads((fixture/"bundle.json").read_text())
    assert all("diagnostics" not in span for span in payload["spans"])
    bundle=ReviewBundle.model_validate(payload)
    assert bundle.bundle_hash=="9dde844428f8f6114e9b8cb088aefa123f79e1ea1c637b228f2ab82f26092052"
    shutil.copytree(fixture/"store",tmp_path/"store")
    store=FileStore(tmp_path/"store")
    before=store.load_task(bundle.bundle_id)
    submitted=store.export_submission(bundle.bundle_id,1).model_dump(mode="json")
    assert store.register(bundle)==before
    assert store.export_submission(bundle.bundle_id,1).model_dump(mode="json")==submitted
    assert submitted["judgments"]["support:margin"]["value"]=="supported"
    # An old browser/API consumer explicitly including null is equivalent to omission.
    for span in payload["spans"]: span["diagnostics"]=None
    assert ReviewBundle.model_validate(payload).bundle_hash==bundle.bundle_hash
    store.save_snapshot(bundle,1,"legacy-resume",before["answers"],"Synthetic legacy reviewer")
    assert store.export_submission(bundle.bundle_id,1).model_dump(mode="json")==submitted


def test_supplied_diagnostics_remain_part_of_store_identity(tmp_path):
    from evidence_review import Conflict
    bundle=diagnostic_bundle()
    store=FileStore(tmp_path);store.register(bundle)
    changed=bundle.model_copy(deep=True)
    next(s for s in changed.spans if s.diagnostics).diagnostics.candidate[0].reason="Different mechanical evidence"
    assert changed.bundle_hash != bundle.bundle_hash
    with pytest.raises(Conflict,match="bundle or rubric changed"):store.register(changed)
