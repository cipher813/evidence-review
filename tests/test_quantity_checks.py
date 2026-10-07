"""Quantity checks are explicit human judgments, preserved across reloads."""
import pytest
from playwright.sync_api import sync_playwright, expect
from evidence_review import FileStore, open_review
from evidence_review.contracts import FormField, validate_bundle
from evidence_review.example import example_bundle

def quantity_bundle():
    b = example_bundle().model_dump(mode="json")
    span = next(s for s in b["spans"] if s["claim_ids"] and s["state"] != "identifier")
    subject = span["claim_ids"][0]
    b["form"][0]["numeric_verification_values"] = ["supported"]
    b["form"].append(dict(field_id="quantity", label="Verified quantity " + span["text"],
        kind="boolean", required=False, subject_id=subject, numeric_span_id=span["span_id"]))
    return validate_bundle(b)

def test_quantity_binding_refuses_missing_or_identifier():
    data = quantity_bundle().model_dump(mode="json")
    data["form"][-1]["numeric_span_id"] = "missing"
    with pytest.raises(ValueError, match="quantity"):
        validate_bundle(data)

def test_unchecked_quantity_blocks_verified_verdict_but_allows_partial(tmp_path):
    b = quantity_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    answers = {"judgments": {"support:margin": {"value": "supported"}, "report_complete": {"value": True}}, "defects": []}
    with pytest.raises(ValueError, match="quantity"):
        store.save_submission(b, 0, "unchecked", answers, "Ada", 1)
    answers["judgments"]["quantity"] = {"value": False}
    with pytest.raises(ValueError, match="quantity"):
        store.save_submission(b, 0, "false", answers, "Ada", 1)
    answers["judgments"]["quantity"]["value"] = True
    saved = store.save_submission(b, 0, "checked", answers, "Ada", 1)
    assert store.export_submission(b.bundle_id, saved["revision"]).judgments["quantity"].value is True

def test_inline_quantity_checkbox_syncs_persists_and_can_be_cleared(tmp_path):
    b = quantity_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.locator("#assessor").fill("Ada")
        checks = page.locator('input[data-quantity-field="quantity"]')
        expect(checks.first).not_to_be_checked()
        with page.expect_response(lambda r: r.url.endswith("/api/save")) as saved:
            checks.first.check()
        assert saved.value.ok, saved.value.text()
        for check in checks.all():
            expect(check).to_be_checked()
        expect(page.locator("#status")).to_contain_text("revision 1")
        page.reload()
        expect(page.locator('input[data-quantity-field="quantity"]').first).to_be_checked()
        with page.expect_response(lambda r: r.url.endswith("/api/save")) as saved:
            page.locator('input[data-quantity-field="quantity"]').first.uncheck()
        assert saved.value.ok, saved.value.text()
        expect(page.locator("#status")).to_contain_text("revision 2")
        page.reload()
        expect(page.locator('input[data-quantity-field="quantity"]').first).not_to_be_checked()
        browser.close()

def test_legacy_bundle_hash_does_not_change_when_quantity_fields_are_unused():
    from evidence_review.contracts import digest
    b = example_bundle()
    original = b.model_dump(mode="json")
    for field in original["form"]:
        field.pop("numeric_span_id", None)
        field.pop("numeric_verification_values", None)
    for span in original["spans"]:
        if not span["prepared_inputs"]:
            del span["prepared_inputs"]
        if span["diagnostics"] is None:
            del span["diagnostics"]
    assert b.bundle_hash == digest(original)
    assert validate_bundle(original).bundle_hash == digest(original)
