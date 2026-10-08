"""Real Chromium: reviewer calculation worksheets are authored, recomputed and kept exactly."""

import json

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, export_json, open_review
from evidence_review.evidence import passage
from test_reviewer_worksheet import worksheet_bundle

LINE = {4: "L4: | Metric | FY2025 | FY2026 |", 6: "L6: | Operating margin | 22.8% | 24.6% |",
        7: "L7: | Revenue ($m) | 1,200 | 1,150 |"}
TERM = {4: "FY2025 | FY2026", 6: "Operating margin", 7: "Revenue ($m)"}


def select_line(page, n):
    """Open the frozen source at line n and select exactly that line."""
    page.get_by_label("Search frozen sources (press /)").fill(TERM[n])
    page.get_by_role("button", name=f"Synthetic filing {LINE[n]}", exact=True).click()
    line = page.get_by_role("region", name="Evidence").get_by_role("button", name=LINE[n], exact=True).first
    line.click()
    line.click()
    expect(page.locator("#status")).to_contain_text(f"Selected L{n}–L{n}")


def saved(page, revision):
    expect(page.locator("#status")).to_contain_text(f"Saved locally · revision {revision}")


def operand(page, w, j, **fields):
    for key, value in fields.items():
        page.get_by_label(f"Worksheet {w} operand {j} {key}", exact=True).fill(value)


def test_worksheet_without_supplied_formula_survives_restart_revision_and_export(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        saved(page, 0)
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_role("button", name="Add calculation worksheet", exact=True).click()
        sheet = page.get_by_role("group", name="Worksheet 1")
        page.get_by_label("Worksheet 1 subject", exact=True).select_option(label="Claim revenue: Revenue fell 4.2%.")
        expect(sheet).to_contain_text("No supplied formula for this subject.")
        page.get_by_label("Worksheet 1 formula", exact=True).fill("(current - prior) / prior * 100")
        for _ in range(2):
            page.get_by_role("button", name="Add operand to worksheet 1", exact=True).click()
        operand(page, 1, 1, name="current", value="1150", unit="$m", period="FY2026", entity="Synthetic issuer", metric="revenue")
        operand(page, 1, 2, name="prior", value="1200", unit="$m", period="FY2025", entity="Synthetic issuer", metric="revenue")
        # Two passages on one input (the row and its header), one on the other.
        select_line(page, 7)
        page.get_by_role("button", name="Attach selected passage to worksheet 1 operand 1", exact=True).click()
        select_line(page, 4)
        page.get_by_role("button", name="Attach selected passage to worksheet 1 operand 1", exact=True).click()
        select_line(page, 7)
        page.get_by_role("button", name="Attach selected passage to worksheet 1 operand 2", exact=True).click()
        page.get_by_label("Worksheet 1 reported value", exact=True).fill("-4.2")
        page.get_by_label("Worksheet 1 unit", exact=True).fill("pct")
        page.get_by_label("Worksheet 1 tolerance", exact=True).fill("0.05")
        result = page.get_by_label("Worksheet 1 result", exact=True)
        expect(result).to_contain_text("Recomputed (Decimal): -4.16666")
        expect(result).to_contain_text("reported value match")
        expect(result).to_contain_text("does not establish support")
        page.get_by_label("Worksheet 1 rationale", exact=True).fill("Decline recomputed from the revenue row.")
        # A second worksheet with an input the sources do not hold.
        page.get_by_role("button", name="Add calculation worksheet", exact=True).click()
        page.get_by_label("Worksheet 2 subject", exact=True).select_option(label="Claim revenue: Revenue fell 4.2%.")
        page.get_by_label("Worksheet 2 formula", exact=True).fill("segment / total * 100")
        for _ in range(2):
            page.get_by_role("button", name="Add operand to worksheet 2", exact=True).click()
        operand(page, 2, 1, name="segment")
        page.get_by_label("Worksheet 2 operand 1 availability", exact=True).select_option("unavailable")
        page.get_by_label("Worksheet 2 operand 1 unavailable reason", exact=True).fill("No segment table in the sources.")
        operand(page, 2, 2, name="total", value="1150")
        select_line(page, 7)
        page.get_by_role("button", name="Attach selected passage to worksheet 2 operand 2", exact=True).click()
        page.get_by_label("Worksheet 2 rationale", exact=True).fill("Share cannot be recomputed.")
        expect(page.get_by_label("Worksheet 2 result", exact=True)).to_contain_text("Incomplete: unavailable input: segment")
        expect(page.get_by_label("Worksheet 2 reviewer support judgment", exact=True)).to_have_value("unknown")
        expect(page.locator("#status")).not_to_contain_text("Saving")
        draft = store.load_task(b.bundle_id)["answers"]["worksheets"]
        assert [w["computation"]["status"] for w in draft] == ["computed", "incomplete"]
        assert len(draft[0]["operands"][0]["selections"]) == 2
        assert draft[0]["operands"][0]["selections"][1]["excerpt"] == passage(b.sources[0], 4, 4)["text"]
        assert draft[1]["operands"][0] == {"name": "segment", "value": "", "unit": "", "period": "", "entity": "",
                                           "metric": "", "availability": "unavailable",
                                           "unavailable_reason": "No segment table in the sources.", "selections": []}
        assert draft[1]["reviewer_support"] == "unknown"
        browser.close()
    # Restart: a new server process over the same store restores the exact worksheet.
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.get_by_label("Worksheet 1 formula", exact=True)).to_have_value("(current - prior) / prior * 100")
        expect(page.get_by_label("Worksheet 1 operand 1 metric", exact=True)).to_have_value("revenue")
        expect(page.get_by_role("group", name="Worksheet 1")).to_contain_text("filing L4–L4")
        expect(page.get_by_label("Worksheet 1 result", exact=True)).to_contain_text("Recomputed (Decimal): -4.16666")
        expect(page.get_by_label("Worksheet 2 result", exact=True)).to_contain_text("Incomplete: unavailable input: segment")
        expect(page.get_by_label("Worksheet 2 operand 1 unavailable reason", exact=True)).to_have_value("No segment table in the sources.")
        page.get_by_label("Revenue support", exact=True).select_option("supported")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        expect(page.locator("#status")).to_contain_text("Submitted")
        state = FileStore(tmp_path).load_task(b.bundle_id)
        first = state["last_submission"]
        exported = json.loads(export_json(FileStore(tmp_path), b.bundle_id, first))
        assert exported["worksheets"] == draft
        # Revision: amend the tolerance; the earlier submission keeps its exact calculation.
        page.get_by_label("Worksheet 1 tolerance", exact=True).fill("0.001")
        expect(page.get_by_label("Worksheet 1 result", exact=True)).to_contain_text("reported value mismatch")
        page.get_by_label("Amendment reason", exact=True).fill("Tighter tolerance")
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text(f"submitted revision {first + 2}")
        store = FileStore(tmp_path)
        assert json.loads(export_json(store, b.bundle_id, first)) == exported
        amended = store.export_submission(b.bundle_id, first + 2).model_dump(mode="json")["worksheets"]
        assert amended[0]["tolerance"] == "0.001" and amended[0]["computation"]["comparison"] == "mismatch"
        assert amended[1] == draft[1]
        browser.close()


def test_wrong_supplied_formula_is_corrected_in_a_worksheet_without_altering_it(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        saved(page, 0)
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        report = page.get_by_role("region", name="Report", exact=True)
        report.get_by_role("button", name="180 bps, derived", exact=True).click()
        page.get_by_role("button", name="Recompute in a reviewer worksheet", exact=True).click()
        sheet = page.get_by_role("group", name="Worksheet 1")
        expect(sheet).to_contain_text("Supplied formula (candidate, unchanged): (current - prior) * 10 → reported 180 bps")
        formula = page.get_by_label("Worksheet 1 formula", exact=True)
        expect(formula).to_have_value("(current - prior) * 10")
        result = page.get_by_label("Worksheet 1 result", exact=True)
        expect(result).to_contain_text("Recomputed (Decimal): 18.0 · reported value mismatch")
        formula.fill("(current - prior) * 100")
        expect(result).to_contain_text("Recomputed (Decimal): 180.0 · reported value match")
        for j in (1, 2):
            select_line(page, 6)
            page.get_by_role("button", name=f"Attach selected passage to worksheet 1 operand {j}", exact=True).click()
        page.get_by_label("Worksheet 1 rationale", exact=True).fill("The supplied formula scales by 10.")
        expect(page.locator("#status")).not_to_contain_text("Saving")
        good = store.load_task(b.bundle_id)
        # An unsafe expression is refused by the server and never replaces the saved one.
        formula.fill("current ** 2")
        expect(page.locator("#status")).to_contain_text("unsupported formula syntax")
        expect(result).to_contain_text("Not recomputed yet")
        assert store.load_task(b.bundle_id)["answers"]["worksheets"][0]["formula"] == "(current - prior) * 100"
        assert store.load_task(b.bundle_id)["revision"] == good["revision"]
        # Division by zero is saved as an explicit calculation error, not coerced.
        formula.fill("current / (prior - prior)")
        expect(result).to_contain_text("Calculation error: division by zero")
        kept = store.load_task(b.bundle_id)["answers"]["worksheets"][0]
        assert kept["computation"]["status"] == "calculation_error" and kept["computation"]["result"] is None
        formula.fill("(current - prior) * 100")
        expect(result).to_contain_text("reported value match")
        # The supplied candidate calculation is exactly as served.
        served = page.evaluate("fetch('/api/bundle', {headers: {Authorization: 'Bearer ' + sessionStorage.getItem('review-token')}}).then(r => r.json())")
        assert served == b.model_dump(mode="json")
        assert served["claims"][0]["calculation"]["formula"] == "(current - prior) * 10"
        report.get_by_role("button", name="180 bps, derived", exact=True).click()
        expect(page.locator(".calculation-card")).to_contain_text("Formula: (current - prior) * 10")
        page.get_by_label("Revenue support", exact=True).select_option("cannot determine")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        sub = store.export_submission(b.bundle_id, store.load_task(b.bundle_id)["last_submission"])
        w = sub.worksheets[0]
        assert (w.subject.kind, w.subject.id) == ("span", "summary:22:29")
        assert w.formula == "(current - prior) * 100" and w.authorship == "reviewer"
        assert w.computation["comparison"] == "match" and w.reviewer_support == "unknown"
        browser.close()
