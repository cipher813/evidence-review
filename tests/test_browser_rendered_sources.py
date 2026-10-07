"""One click opens the exact rendered target; nothing executes, leaves the machine or leaks the token."""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import build_atom_manifest
from evidence_review.contracts import Claim, validate_bundle
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset
from rendered_fixtures import html_bundle, html_sidecars, read
from synthetic import build, located, source

ATOMIC = "atomic-source-check/v1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def watched(browser, h, **context):
    ctx = browser.new_context(**context)
    page = ctx.new_page()
    log = {"requests": [], "errors": [], "dialogs": []}
    page.on("request", lambda r: log["requests"].append(r.url))
    page.on("pageerror", lambda e: log["errors"].append(str(e)))
    page.on("dialog", lambda d: (log["dialogs"].append(d.message), d.dismiss()))
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    return ctx, page, log


def assert_offline(h, page, log):
    assert all(u.startswith(h.origin + "/") for u in log["requests"]), log["requests"]
    assert not any(h.token in u for u in log["requests"][1:])
    assert h.token not in page.url and "token" not in page.url
    assert not log["errors"] and not log["dialogs"]


def single_source_bundle(text, cite, bundle_id):
    b = build([("summary", "Margin was 24.6%.", ["c"])],
              [Claim(claim_id="c", text="Margin was 24.6%.", citations=[cite])],
              sources=[source(text)], task_kind="reference", bundle_id=bundle_id)
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="cited", claim_ids=["c"], citations=[cite.model_dump(mode="json")])
    return validate_bundle(data)


def test_html_table_one_click_exact_cell_with_headers_and_notes(tmp_path, browser):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        row = page.locator('[data-atom-row]').filter(has_text="“24.6%”")
        row.get_by_role("button", name="Open the source").click()
        hit = page.locator("#viewer .target-hit")
        expect(hit).to_have_count(1)
        expect(hit).to_contain_text("24.6%")
        expect(hit).to_be_in_viewport()
        expect(page.locator("#viewer .target-status")).to_contain_text("Exact location highlighted")
        expect(page.locator("#viewer mark.cited-words")).to_have_text("24.6%")
        # The highlighted cell keeps its column header, row label, caption and footnote in view of the reader.
        expect(page.locator("#viewer table caption")).to_contain_text("Segment results by fiscal year")
        expect(page.locator("#viewer th").filter(has_text="FY2026")).to_have_count(1)
        assert hit.evaluate("n => n.parentElement.querySelector('th').textContent") == "Operating margin"
        expect(page.locator("#viewer")).to_contain_text("(1) Revenue restated")
        assert hit.evaluate("n => getComputedStyle(n).outlineStyle") == "solid"  # Not colour alone.
        expect(page.locator("#viewer .target-flag")).to_have_text("▶ cited")
        page.locator("#viewer").get_by_text("Rendering: faithful html").click()
        expect(page.locator("#viewer .render-fidelity")).to_contain_text("Original SHA-256")
        assert_offline(h, page, log)
        ctx.close()


def test_hostile_html_renders_inert_with_zero_outbound_requests(tmp_path, browser):
    text = read("hostile.frozen.md").decode()
    b = single_source_bundle(text, located(4, "310"), "hostile")
    asset = prepare_render_asset(b, "filing", OriginalAsset(read("hostile.html"), "text/html"))
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        page.locator(".atom-link").first.click()
        expect(page.locator("#viewer .rendered-doc")).to_contain_text("Management expects demand")
        expect(page.locator("#viewer .rendered-doc")).to_contain_text("[image omitted from offline rendering]")
        assert page.locator("#viewer script, #viewer iframe, #viewer img, #viewer form, #viewer svg, #viewer a").count() == 0
        assert page.evaluate("[...document.querySelectorAll('#viewer *')].every(n => ![...n.attributes].some(a => a.name.startsWith('on') || a.name === 'href' || a.name === 'src'))")
        page.wait_for_timeout(300)
        assert_offline(h, page, log)
        ctx.close()


def test_pdf_page_and_box_highlight_and_scanned_page_only(tmp_path, browser):
    frozen = read("two-page.frozen.txt").decode()
    b = single_source_bundle(frozen, located(6, "24.6%"), "pdf")
    asset = prepare_render_asset(b, "filing", OriginalAsset(read("two-page.pdf"), "application/pdf"))
    with open_review(b, FileStore(tmp_path / "pdf"), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        page.locator(".atom-link").first.click()
        hit = page.locator("#viewer .pdf-page[data-page='2'] .target-hit")
        expect(hit).to_have_count(1)
        expect(hit).to_contain_text("Retail margin was 24.6% in FY2025.")
        expect(hit).to_be_in_viewport()
        expect(page.locator("#viewer .pdf-page")).to_have_count(2)
        assert_offline(h, page, log)
        ctx.close()
    scanned = single_source_bundle(read("scanned.frozen.txt").decode(), located(1, "24.6%"), "scanned")
    asset = prepare_render_asset(scanned, "filing", OriginalAsset(read("scanned.pdf"), "application/pdf", page_map={1: 1}))
    with open_review(scanned, FileStore(tmp_path / "scan"), launch=False, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        page.locator(".atom-link").first.click()
        expect(page.locator("#viewer .target-status")).to_contain_text("Page-only location")
        expect(page.locator("#viewer .pdf-page.target-page")).to_have_count(1)
        expect(page.locator("#viewer .target-hit")).to_have_count(0)
        expect(page.locator("#viewer")).to_contain_text("no extractable text layer")
        assert_offline(h, page, log)
        ctx.close()


def test_markdown_escaped_pipes_stay_numbered_text_with_exact_line(tmp_path, browser):
    raw = read("escaped.md")
    b = single_source_bundle(raw.decode(), located(4, "24.6%"), "escaped")
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "text/markdown"))
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        page.locator(".atom-link").first.click()
        hit = page.locator("#viewer .target-hit")
        expect(hit).to_have_attribute("data-line", "4")
        expect(hit).to_contain_text(r"24.6% \| reported")
        assert page.locator("#viewer table").count() == 0
        page.locator("#viewer").get_by_text("Rendering: faithful markdown").click()
        expect(page.locator("#viewer .render-fidelity")).to_contain_text("not proven")
        assert_offline(h, page, log)
        ctx.close()


def test_ambiguous_atom_shows_every_candidate_and_chooses_none(tmp_path, browser):
    text = "| Metric | FY2025 | FY2026 |\n| --- | --- | --- |\n| A | 24.6% | 1 |\n| B | 24.6% | 2 |\n"
    cites = [located(3, "24.6%"), located(4, "24.6%")]
    b = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="Margin was 24.6%.", citations=cites)],
              sources=[source(text)], task_kind="reference", bundle_id="ambiguous")
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="ambiguous", claim_ids=["c"], reason="Two rows hold this value",
                 citations=[c.model_dump(mode="json") for c in cites])
    b = validate_bundle(data)
    atoms = build_atom_manifest(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, presentation_mode=ATOMIC) as h:
        ctx, page, log = watched(browser, h)
        row = page.locator("[data-atom-row]").first
        expect(row).to_contain_text("Ambiguous: several candidate sources, none chosen")
        expect(row.locator(".atom-link")).to_have_count(0)
        row.locator("summary").click()
        expect(row).to_contain_text("Candidate 1 of 2")
        expect(row).to_contain_text("Candidate 2 of 2")
        row.get_by_role("button", name="Open candidate 2").click()
        expect(page.locator("#viewer .target-status")).to_contain_text("Candidate 2 of 2; no source was chosen")
        assert_offline(h, page, log)
        ctx.close()
