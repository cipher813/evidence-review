"""Rendered documents stay inside their viewer when evidence details consume pane height."""
import pytest
from playwright.sync_api import expect, sync_playwright
from test_long_table_context import long_table_review
from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, validate_bundle
from synthetic import build, located, source

@pytest.mark.parametrize("kind", ["table", "transcript"])
@pytest.mark.parametrize("size", [(1400, 700), (1100, 600), (900, 700)])
def test_rendered_source_never_overlaps_following_evidence_controls(tmp_path, size, kind):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        if kind == "table":
            bundle, handle = long_table_review(tmp_path, original=False)
        else:
            line = "Speaker: Revenue was 42; the lengthy explanation wraps over many lines. " * 12
            text = "# Synthetic transcript\n" + (line + "\n") * 30
            cite = located(15, "42")
            bundle = build([("summary", "Revenue was 42.", ["c"])],
                           [Claim(claim_id="c", text="Revenue was 42.", citations=[cite])],
                           sources=[source(text)])
            data = bundle.model_dump(mode="json")
            for span in data["spans"]:
                span.update(state="cited", claim_ids=["c"], citations=[cite.model_dump(mode="json")])
            bundle = validate_bundle(data)
            handle = open_review(bundle, FileStore(tmp_path), launch=False,
                                 presentation_mode="atomic-source-check/v1")
        with handle:
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            page.goto(handle.url)
            expect(page.locator("#status")).to_contain_text("Saved locally")
            page.locator("[data-atom-row] .atom-link").first.click()
            expect(page.locator("#viewer .target-hit")).to_have_count(1)
            # The source panel is independently expandable; it must not overlap the viewer.
            for expanded, fidelity in ((True, False), (False, False), (False, True), (True, True)):
                for selector, opened in (("#evidence-panel", expanded), ("#viewer .render-fidelity", fidelity)):
                    if (page.locator(selector).get_attribute("open") is not None) != opened:
                        page.locator(selector + " > summary").click()
                bounds = page.locator("#viewer").evaluate("""n => {
                    const r = n.getBoundingClientRect();
                    const doc = n.querySelector('.rendered-doc').getBoundingClientRect();
                    const next = document.querySelector('#evidence-panel').getBoundingClientRect();
                    return {viewerBottom:r.bottom, docBottom:doc.bottom, nextTop:next.top,
                            scroll:n.querySelector('.rendered-doc').scrollHeight,
                            height:doc.height};
                }""")
                assert bounds["docBottom"] <= bounds["viewerBottom"], bounds
                assert bounds["docBottom"] < bounds["nextTop"], bounds
                assert bounds["scroll"] > bounds["height"], bounds
                expect(page.locator("#status")).to_contain_text("revision 0")
            page.close()
        browser.close()
