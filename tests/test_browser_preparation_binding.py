"""Audit regressions (evidence-review-I31, I32) in Chromium; every value is synthetic.

I31: the frozen check bound to an assigned atom is reachable from its row, gates the support verdict, submits and
survives reload. I32: an occurrence-bound prepared calculation is visible and navigable in its row, labelled as
preparation, beside the original candidate evidence, and opening it never checks the row.
"""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review, validate_bundle
from evidence_review.atomic_evidence import build_atom_manifest
from evidence_review.contracts import PreparedEvidence
from rendered_fixtures import html_bundle, html_sidecars
from test_form_binding_and_preparation import checked_revenue, prepared_margin

ATOMIC = "atomic-source-check/v1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def opened(browser, h):
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    page.locator("#assessor").fill("Ada")
    return ctx, page, errors


def test_bound_check_is_reachable_gates_support_submits_and_reloads(tmp_path, browser):
    b = checked_revenue()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, atom_evidence=build_atom_manifest(b), presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        row = page.locator("[data-atom-row]")
        expect(row).to_have_count(1)
        expect(row).not_to_contain_text("No check control assigned")
        check = row.locator('input[data-quantity-field="quantity:check"]')
        expect(check).to_have_count(1)
        page.locator("#field-support\\:c").select_option("supported")
        page.locator("#field-report_complete").check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as refused:
            page.locator("#submit").click()
        assert refused.value.status == 400
        with page.expect_response(lambda r: r.url.endswith("/api/save")):
            check.check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as accepted:
            page.locator("#submit").click()
        assert accepted.value.ok, accepted.value.text()
        revision = accepted.value.json()["revision"]
        page.reload()
        expect(page.locator("#status")).to_contain_text("Saved locally")
        expect(check).to_be_checked()
        expect(page.locator("#field-support\\:c")).to_have_value("supported")
        submitted = store.export_submission(b.bundle_id, revision)
        assert submitted.judgments["quantity:check"].value is True
        assert submitted.judgments["support:c"].value == "supported"
        assert not errors
        ctx.close()


def test_prepared_only_row_shows_and_navigates_preparation_without_checking(tmp_path, browser):
    b = prepared_margin(claim_ids=["c"])
    d = b.model_dump(mode="json")
    d["form"].append(dict(field_id="quantity:margin", label="Checked 180 bps", kind="boolean", required=False,
                          subject_id="c", numeric_span_id=b.spans[0].span_id))
    b = validate_bundle(d)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=build_atom_manifest(b),
                     presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        row = page.locator("[data-atom-row]")
        expect(row).to_contain_text("No source located")  # Original candidate state, unchanged.
        expect(row).to_contain_text("Independently prepared evidence is available")
        row.locator("summary").first.click()
        prep = row.locator("[data-prepared-ref]")
        expect(prep).to_have_count(1)
        for text in ("Independently prepared calculation", "not a support judgment", "Independent complete calculation",
                     "(24.6 - 22.8) * 100", "reported 180 bps", "Prepared input 1:", "Prepared input 2:",
                     "Reference prepared:"):
            expect(prep).to_contain_text(text)
        expect(row).not_to_contain_text("Original candidate calculation")
        button = prep.get_by_role("button", name="Open prepared input 2 source")
        button.click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("22.8%")
        expect(page.locator("#viewer")).to_contain_text("Input of an independently prepared calculation")
        page.locator("#viewer").get_by_role("button", name="Back to row").click()
        expect(button).to_be_focused()
        expect(row.locator('input[data-quantity-field="quantity:margin"]')).not_to_be_checked()
        assert not errors
        ctx.close()


def test_original_and_prepared_calculations_are_labelled_separately(tmp_path, browser):
    b = html_bundle()
    d = b.model_dump(mode="json")
    calc = next(s for s in d["spans"] if s["text"] == "180 bps")
    calc["prepared_evidence"] = [PreparedEvidence(calculation=b.claims[0].calculation.model_copy(
        update={"result": "170"}), reason="Second method").model_dump(mode="json")]
    b = validate_bundle(d)
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        row = page.locator("[data-atom-row]").filter(has_text="“180 bps”")
        expect(row).to_contain_text("Calculated from inputs")
        row.locator("summary").first.click()
        expect(row).to_contain_text("Original candidate calculation")
        expect(row).to_contain_text("reported 180 bps")
        prep = row.locator("[data-prepared-ref]")
        expect(prep).to_contain_text("reported 170 bps")
        expect(prep).not_to_contain_text("reported 180 bps")
        row.get_by_role("button", name="Open input 1 source").click()  # The original's own leaves still navigate.
        expect(page.locator("#viewer .target-hit")).to_contain_text("24.6%")
        prep.get_by_role("button", name="Open prepared input 2 source").click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("22.8%")
        expect(row.locator("input[type=checkbox]")).not_to_be_checked()
        assert not errors
        ctx.close()
