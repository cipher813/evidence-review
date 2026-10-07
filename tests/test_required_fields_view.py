"""Required-only presentation preserves frozen contracts and optional saved evidence."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import FormField
from test_browser_guided import guided_bundle
from test_reference_layout import reference_quantity_bundle
from test_server import request


def optional_bundle():
    bundle = guided_bundle()
    for field in bundle.form:
        if field.subject_id:
            field.required = False
    bundle.form.append(FormField(field_id="optional_context", label="Optional context",
                                 kind="text", required=False))
    return bundle


@pytest.mark.parametrize("enabled,kind,effective", [
    (False, "independent", False), (True, "independent", True),
    (True, "reference", False), (True, "adjudication", False),
])
def test_presentation_sidecar_preserves_bundle_identity(tmp_path, enabled, kind, effective):
    bundle = optional_bundle()
    bundle.task_kind = kind
    original = bundle.model_dump(mode="json")
    original_hash = bundle.bundle_hash
    with open_review(bundle, FileStore(tmp_path), launch=False, required_fields_only=enabled) as handle:
        with request(handle, "/api/evidence") as response:
            assert json.load(response)["presentation"]["required_fields_only"] is effective
        with request(handle, "/api/bundle") as response:
            assert json.load(response) == original
    assert bundle.bundle_hash == original_hash


def test_presentation_option_rejects_nonboolean_values(tmp_path):
    with pytest.raises(TypeError, match="required_fields_only"):
        open_review(optional_bundle(), FileStore(tmp_path), launch=False, required_fields_only="false")


@pytest.mark.parametrize("enabled", [False, True])
def test_required_view_counts_only_visible_work_and_preserves_hidden_answers(tmp_path, enabled):
    bundle = optional_bundle()
    original_hash = bundle.bundle_hash
    store = FileStore(tmp_path)
    store.register(bundle)
    saved = {"judgments": {
        "coverage:R1": {"value": "", "note": "Keep unfinished diagnostic", "claim_ids": ["C1"]},
        "optional_context": {"value": "Preserve saved optional context"}},
        "defects": []}
    store.save_snapshot(bundle, 0, "original", saved, "Synthetic reviewer")
    original_answers = store.load_task(bundle.bundle_id)["answers"]["judgments"]
    kwargs = {"required_fields_only": True} if enabled else {}
    workload = {"answer_index": 1, "assigned_answers": 9, "task_counts": {"independent": 9}}
    with open_review(bundle, store, launch=False, workload=workload, **kwargs) as handle, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(handle.url)
        expect(page.locator("header").get_by_role("heading", name="Answer grading", exact=True)).to_be_visible()
        expect(page.locator("#task")).to_have_text(bundle.bundle_id)
        expect(page.locator("header")).to_contain_text("Answer 1 of 9")
        if enabled:
            expect(page.locator("#status")).to_contain_text("0/2 required fields answered")
            expect(page.locator("#progress")).not_to_contain_text("optional")
            expect(page.locator("#item-links button")).to_have_count(0)
            expect(page.get_by_label("Optional context", exact=True)).to_have_count(0)
        else:
            expect(page.locator("#status")).to_contain_text("2/31 fields answered")
            expect(page.locator("#progress")).to_contain_text("29 optional fields")
            expect(page.locator("#item-links button")).to_have_count(28)
            expect(page.get_by_label("Optional context", exact=True)).to_have_value("Preserve saved optional context")
        expect(page.get_by_role("button", name="Add defect", exact=True)).to_be_visible()
        page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="24.6%, cited").first.click()
        page.get_by_role("region", name="Evidence", exact=True).get_by_role("button", name="Whole claim: Statement 1:").click()
        expect(page.get_by_role("region", name="Evidence", exact=True)).to_contain_text("L6 (cited)")
        page.get_by_label("Is the full answer assessable?", exact=True).select_option("complete")
        page.get_by_label("Full review complete", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Submitted")
        if enabled:
            expect(page.locator("#status")).to_contain_text("2/2 required fields answered")
        page.reload()
        expect(page.locator("#status")).to_contain_text("Submitted")
        browser.close()
    final = store.load_task(bundle.bundle_id)
    for key, value in original_answers.items():
        assert final["answers"]["judgments"][key] == value
        assert final["submissions"][str(final["last_submission"])]["judgments"][key] == value
    assert final["bundle_hash"] == original_hash == bundle.bundle_hash


def test_hidden_invalid_optional_answer_remains_repairable(tmp_path):
    bundle = optional_bundle()
    store = FileStore(tmp_path)
    store.register(bundle)
    store.save_snapshot(bundle, 0, "incomplete-optional", {"judgments": {
        "coverage:R1": {"value": "missing", "note": ""}}, "defects": []}, "Synthetic reviewer")
    with open_review(bundle, store, launch=False, required_fields_only=True) as handle, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(handle.url)
        expect(page.get_by_label("Coverage: R1", exact=True)).to_have_count(0)
        page.get_by_label("Is the full answer assessable?", exact=True).select_option("complete")
        page.get_by_label("Full review complete", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        page.get_by_role("button", name="Add explanation: Coverage: R1", exact=True).click()
        expect(page.get_by_label("Coverage: R1", exact=True)).to_have_value("missing")
        page.get_by_label("Explanation: Coverage: R1", exact=True).fill("Existing optional decision repaired.")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Submitted")
        browser.close()
    assert store.load_task(bundle.bundle_id)["answers"]["judgments"]["coverage:R1"]["value"] == "missing"


def test_required_only_never_hides_reference_quantity_checks(tmp_path):
    bundle = reference_quantity_bundle()
    with open_review(bundle, FileStore(tmp_path), launch=False, required_fields_only=True) as handle, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(handle.url)
        expect(page.locator("header").get_by_role("heading", name="Source verification", exact=True)).to_be_visible()
        expect(page.locator("#task")).to_have_text(bundle.bundle_id)
        expect(page.locator("#items input[data-quantity-field]")).to_have_count(1)
        expect(page.locator("#items input[data-quantity-field]").first).to_be_visible()
        browser.close()


@pytest.mark.parametrize("kind,heading", [
    ("adjudication", "Disagreement review"), ("finding", "Finding review"),
])
def test_header_names_the_active_review_without_changing_blinded_id(tmp_path, kind, heading):
    bundle = optional_bundle()
    bundle.task_kind = kind
    original_hash = bundle.bundle_hash
    with open_review(bundle, FileStore(tmp_path), launch=False) as handle, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(handle.url)
        expect(page.locator("header").get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(page.locator("#task")).to_have_text(bundle.bundle_id)
        browser.close()
    assert bundle.bundle_hash == original_hash
