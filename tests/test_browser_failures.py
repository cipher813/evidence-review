from playwright.sync_api import sync_playwright, expect
from evidence_review import open_review
from evidence_review.contracts import ReportField, digest
from evidence_review.evidence import numeric_spans
from evidence_review.example import example_bundle
from evidence_review.store import FileStore
from evidence_review.hooks import Hooks


def test_browser_pending_hook_defect_and_injection(tmp_path):
    b = example_bundle()
    # Deliberately hostile literal text: must remain text even inside numeric highlighting.
    f = ReportField(
        path="hostile",
        label="Untrusted",
        text='<img src=x onerror="window.evidenceInjected=true"> 7%',
    )
    b.fields.append(f)
    b.spans.extend(numeric_spans(f.path, f.text))
    b.document_hashes["report"] = digest([f.model_dump() for f in b.fields])

    def failed(sub):
        return {
            "status": "failed",
            "identifier": None,
            "reason": "synthetic backup failure",
        }

    def recovered(sub):
        return {
            "status": "succeeded",
            "identifier": "synthetic-receipt",
            "reason": "reconciled",
        }

    with (
        open_review(
            b,
            FileStore(tmp_path),
            Hooks(on_submission=failed, reconcile=recovered),
            launch=False,
        ) as h,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.get_by_role("status")).to_contain_text("Saved locally")
        assert page.evaluate("window.evidenceInjected") is None
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_label(
            "Does the evidence support the margin claim?", exact=True
        ).select_option("supported")
        expect(page.get_by_role("status")).to_contain_text("revision 1")
        page.get_by_role("button", name="Add defect", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("revision 2")
        page.get_by_label("Defect category", exact=True).fill("unsupported_conclusion")
        page.get_by_label("Defect category", exact=True).press("Tab")
        expect(page.get_by_role("status")).to_contain_text("revision 3")
        page.get_by_label("Defect materiality", exact=True).select_option("true")
        expect(page.get_by_role("status")).to_contain_text("revision 4")
        page.get_by_label("Defect evidence explanation", exact=True).fill(
            "Synthetic uncited forecast has no support."
        )
        page.get_by_label("Defect evidence explanation", exact=True).press("Tab")
        expect(page.get_by_role("status")).to_contain_text("revision 5")
        page.get_by_label(
            "I reviewed the full report and recorded all identified material defects.",
            exact=True,
        ).check()
        expect(page.get_by_role("status")).to_contain_text("revision 6")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("synthetic backup failure")
        expect(
            page.get_by_role("button", name="Next task", exact=True)
        ).to_be_disabled()
        page.reload()
        expect(page.get_by_role("status")).to_contain_text("synthetic backup failure")
        page.get_by_role("button", name="Reconcile continuation", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("reconciled")
        expect(page.get_by_role("button", name="Next task", exact=True)).to_be_enabled()
        browser.close()


def test_missing_required_answer_can_be_corrected_without_reload(tmp_path):
    b = example_bundle()
    with (
        open_review(b, FileStore(tmp_path), launch=False) as h,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.get_by_role("status")).to_contain_text("Saved locally")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("required field")
        page.get_by_label(
            "Does the evidence support the margin claim?", exact=True
        ).select_option("supported")
        expect(page.get_by_role("status")).to_contain_text("revision 1")
        page.get_by_label(
            "I reviewed the full report and recorded all identified material defects.",
            exact=True,
        ).check()
        expect(page.get_by_role("status")).to_contain_text("revision 2")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("continuation succeeded")
        browser.close()
