"""Synthetic blank-header tables render as grids and navigate to their exact cells."""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset, render_frozen_text
from synthetic import located
from test_browser_rendered_sources import single_source_bundle


@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
def test_blank_corner_table_one_click_opens_exact_original_cell(tmp_path, mode):
    text = ("# Results\n| | FY2025 | FY2026 |\n| --- | --- | --- |\n"
            "| Revenue | 22.8% | 24.6% |\n| Margin | 21.1% | 24.6% |\n"
            "Note: synthetic figures only.\n")
    bundle = single_source_bundle(text, located(4, "24.6%"), "blank-corner-" + mode.replace("_", "-"))
    asset = (render_frozen_text(bundle, "filing") if mode == "normalized_snapshot"
             else prepare_render_asset(bundle, "filing", OriginalAsset(text.encode(), "text/markdown")))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        with open_review(bundle, FileStore(tmp_path), launch=False, rendered_sources=[asset],
                         presentation_mode="atomic-source-check/v1") as handle:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(handle.url)
            expect(page.locator("#status")).to_contain_text("Saved locally")
            page.locator("[data-atom-row] .atom-link").first.click()
            table = page.locator("#viewer table")
            expect(table).to_have_count(1)
            expect(table.locator("thead th")).to_have_text(["", "FY2025", "FY2026"])
            expect(table.locator("tbody tr")).to_have_count(2)
            hit = table.locator(".target-hit")
            expect(hit).to_have_count(1)
            expect(hit).to_contain_text("24.6%")
            expect(hit.locator("mark.cited-words")).to_have_text("24.6%")
            expect(hit).to_be_in_viewport()
            assert hit.evaluate("n => n.parentElement.querySelector('th').textContent") == "Revenue"
            expect(page.locator("#viewer .target-status")).to_contain_text("Exact location highlighted")
            expect(page.locator("#viewer")).to_contain_text("Note: synthetic figures only.")
            expect(page.locator("#status")).to_contain_text("revision 0")
            assert not errors
            page.close()
        browser.close()
