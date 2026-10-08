"""Real Chromium: a reviewer calculation worksheet started from an answer annotation. Synthetic data only."""
import json

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, export_json, open_review
from test_browser_answer_annotations import drag_select, settle
from test_browser_worksheet import operand, select_line
from test_reviewer_worksheet import worksheet_bundle

PROSE = "Revenue fell"


def subject_label(page, n):
    return page.get_by_label(f"Worksheet {n} subject", exact=True).evaluate("s => s.selectedOptions[0].text")


def test_worksheet_from_selected_prose_annotation_survives_restart_and_export(tmp_path):
    b = worksheet_bundle()
    start = b.fields[0].text.index(PROSE)
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        drag_select(page, "summary", start, start + len(PROSE))
        expect(page.get_by_label("Selected answer text", exact=True)).to_contain_text(
            f"characters {start + 1}–{start + len(PROSE)}")
        page.get_by_role("button", name="Annotate selected answer text").click()
        card = page.locator("#annotations .annotation").first
        card.get_by_label("Annotation disposition").select_option("cannot_verify")
        card.get_by_label("Annotation reason").fill("The decline needs recomputing from the table.")
        page.get_by_role("button", name="Start calculation worksheet from annotation 1", exact=True).click()
        sheet = page.get_by_role("group", name="Worksheet 1")
        expect(sheet).to_contain_text("No supplied formula for this subject.")
        assert subject_label(page, 1) == f"Annotation 1: “{PROSE}”"
        page.get_by_label("Worksheet 1 formula", exact=True).fill("(current - prior) / prior * 100")
        for _ in range(2):
            page.get_by_role("button", name="Add operand to worksheet 1", exact=True).click()
        operand(page, 1, 1, name="current", value="1150", unit="$m", period="FY2026", metric="revenue")
        operand(page, 1, 2, name="prior", value="1200", unit="$m", period="FY2025", metric="revenue")
        for j in (1, 2):
            select_line(page, 7)
            page.get_by_role("button", name=f"Attach selected passage to worksheet 1 operand {j}", exact=True).click()
        page.get_by_label("Worksheet 1 reported value", exact=True).fill("-4.2")
        page.get_by_label("Worksheet 1 tolerance", exact=True).fill("0.05")
        page.get_by_label("Worksheet 1 rationale", exact=True).fill("Recomputed the decline the annotation questions.")
        expect(page.get_by_label("Worksheet 1 result", exact=True)).to_contain_text("Recomputed (Decimal): -4.16666")
        # The linked annotation cannot be removed while the worksheet is about it.
        card.get_by_role("button", name="Remove annotation").click()
        expect(card.get_by_role("alert")).to_contain_text(
            "Not removed: worksheet 1 is about this annotation. Change that worksheet's subject or remove it first.")
        settle(page)
        draft = store.load_task(b.bundle_id)["answers"]
        ident = draft["annotations"][0]["annotation_id"]
        assert ident.startswith("ann:") and len(draft["annotations"]) == 1
        assert draft["annotations"][0]["answer_range"]["text"] == PROSE
        assert draft["worksheets"][0]["subject"] == {"kind": "annotation", "id": ident}
        assert draft["worksheets"][0]["computation"]["status"] == "computed"
        browser.close()
    # Restart: a new server process over the same store restores the annotation and its worksheet.
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        card = page.locator("#annotations .annotation").first
        expect(card.get_by_label("Annotation reason")).to_have_value("The decline needs recomputing from the table.")
        expect(page.get_by_label("Worksheet 1 formula", exact=True)).to_have_value("(current - prior) / prior * 100")
        assert subject_label(page, 1) == f"Annotation 1: “{PROSE}”"
        expect(page.get_by_label("Worksheet 1 result", exact=True)).to_contain_text("Recomputed (Decimal): -4.16666")
        page.get_by_label("Revenue support", exact=True).select_option("supported")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        expect(page.locator("#status")).to_contain_text("Submitted")
        browser.close()
    store = FileStore(tmp_path)
    exported = json.loads(export_json(store, b.bundle_id, store.load_task(b.bundle_id)["last_submission"]))
    assert exported["annotations"] == draft["annotations"]
    assert exported["worksheets"] == draft["worksheets"]
    assert exported["worksheets"][0]["subject"] == {"kind": "annotation", "id": ident}
    assert b.fields[0].text.count(PROSE) == 1  # original answer text unchanged
