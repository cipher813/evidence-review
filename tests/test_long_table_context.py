"""One click on a long table keeps the cell's column header, row label and footnote in view (0.5.4).

Also: a missing exact node is never announced as an exact highlight, and every
row's expansion control has its own accessible name."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, validate_bundle
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset
from rendered_fixtures import html_bundle, html_sidecars
from synthetic import build, located, source

ATOMIC = "atomic-source-check/v1"
ROWS = 80


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def long_table_review(tmp_path, original=True):
    rows = "".join(f"| Metric {i} | {i}.1% | {i}.2% |\n" for i in range(1, ROWS))
    text = ("# Results\n\nAll amounts in percent of revenue.\n| Metric | FY2025 | FY2026 |\n| --- | --- | --- |\n"
            + rows + "(1) Metric 70 restated for a disposed segment.\n")
    line = 5 + 70  # "| Metric 70 | 70.1% | 70.2% |"
    assert "Metric 70 " in text.splitlines()[line - 1]
    cite = located(line, "70.2%")
    b = build([("summary", "Margin was 70.2%.", ["c"])],
              [Claim(claim_id="c", text="Margin was 70.2%.", citations=[cite])],
              sources=[source(text)], task_kind="reference", bundle_id="long-table")
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="cited", claim_ids=["c"], citations=[cite.model_dump(mode="json")])
    b = validate_bundle(data)
    assets = [prepare_render_asset(b, "filing", OriginalAsset(text.encode(), "text/markdown"))] if original else []
    return b, open_review(b, FileStore(tmp_path), launch=False, rendered_sources=assets, presentation_mode=ATOMIC)


def in_viewport(locator):
    """Fully inside the window and not clipped by any scrolling ancestor."""
    return locator.evaluate("""n => {
        const r = n.getBoundingClientRect();
        if (r.top < 0 || r.bottom > innerHeight || r.height === 0) return false;
        for (let p = n.parentElement; p; p = p.parentElement) {
            const s = getComputedStyle(p);
            if (!/(auto|scroll|hidden)/.test(s.overflowY)) continue;
            const b = p.getBoundingClientRect();
            if (r.top < b.top - 1 || r.bottom > b.bottom + 1) return false;
        }
        // Not covered by something else (a sticky header over the cell, say).
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 8));
        return !!hit && (n.contains(hit) || hit.contains(n));
    }""")


@pytest.mark.parametrize("original", [True, False], ids=["faithful-markdown", "normalized-snapshot"])
def test_one_click_long_table_keeps_header_row_label_and_footnote_in_view(tmp_path, browser, original):
    b, h = long_table_review(tmp_path, original)
    with h:
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.locator("[data-atom-row] .atom-link").first.click()
        hit = page.locator("#viewer .target-hit")
        expect(hit).to_have_count(1)
        expect(hit).to_contain_text("70.2%")
        assert in_viewport(hit)
        # The column header naming the period is visible together with the hit, not 1,600 px above it.
        header = page.locator("#viewer thead th").filter(has_text="FY2026")
        assert in_viewport(header), "column header scrolled out of view"
        # Status and the verbatim cited-cell context stay in view: column, row label, units and footnote.
        status = page.locator("#viewer .target-status")
        expect(status).to_contain_text("Exact location highlighted")
        assert in_viewport(status)
        context = page.locator("#viewer .target-context")
        for text in ("Column: FY2026", "Row: Metric 70", "All amounts in percent of revenue",
                     "Note: (1) Metric 70 restated for a disposed segment.", "Section: Results"):
            expect(context).to_contain_text(text)
        assert in_viewport(context)
        assert hit.get_attribute("aria-describedby") == "target-context"
        # Screen-reader table association is kept: the header cell is a column header.
        expect(header).to_have_attribute("scope", "col")
        page.close()


def test_html_table_context_names_caption_column_row_and_footnote(tmp_path, browser):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.locator("[data-atom-row]").filter(has_text="“24.6%”").get_by_role("button", name="Open the source").click()
        context = page.locator("#viewer .target-context")
        for text in ("Column: FY2026", "Row: Operating margin", "Caption: Table 1. Segment results by fiscal year",
                     "Note: (1) Revenue restated for a disposed segment."):
            expect(context).to_contain_text(text)
        page.close()


def test_missing_exact_node_is_shown_as_unavailable_not_exact(tmp_path, browser):
    """Defence in depth behind the server's refusal: a response whose exact node is absent highlights nothing."""
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        page = browser.new_page()

        def tamper(route):
            response = route.fetch()
            body = response.json()
            for t in body["manifest"]["targets"]:
                if t["status"] == "exact":
                    t["dom_targets"] = ["n-missing"]
            route.fulfill(response=response, body=json.dumps(body))

        page.route("**/api/source-render/**", tamper)
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.locator("[data-atom-row]").filter(has_text="“24.6%”").get_by_role("button", name="Open the source").click()
        status = page.locator("#viewer .target-status")
        expect(status).to_contain_text("Location unavailable")
        expect(status).not_to_contain_text("Exact location highlighted")
        expect(page.locator("#viewer .target-hit")).to_have_count(0)
        page.close()


def test_every_row_expansion_has_a_distinct_accessible_name(tmp_path, browser):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        rows = page.locator("[data-atom-row]")
        names = []
        for i in range(rows.count()):
            row = rows.nth(i)
            summary = row.locator("summary.atom-summary")
            name = summary.get_attribute("aria-label")
            text = row.get_attribute("aria-label").split(": ", 1)[1]
            assert text in name, (name, text)
            names.append(name)
            others = [row.locator(sel).first.get_attribute("aria-label")
                      for sel in ("input[type=checkbox]", ".atom-link") if row.locator(sel).count()]
            assert name not in others
        assert len(names) >= 3 and len(set(names)) == len(names), names
        calc = rows.filter(has_text="“180 bps”")
        expect(page.get_by_text("Calculation, inputs and sources").first).to_be_visible()
        calc.locator("summary").first.focus()
        page.keyboard.press("Enter")
        expect(calc.locator("details.atom-expand")).to_have_attribute("open", "")
        assert "“180 bps”" in calc.locator("summary").first.get_attribute("aria-label")
        page.close()
