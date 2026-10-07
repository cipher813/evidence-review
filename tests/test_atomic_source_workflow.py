"""atomic-source-check/v1: statements above, one row per atom left, rendered source right; clicks never check."""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review, validate_bundle
from rendered_fixtures import FACT, STATEMENT, html_bundle, html_sidecars

ATOMIC = "atomic-source-check/v1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def opened(browser, h, **context):
    ctx = browser.new_context(**context)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    page.locator("#assessor").fill("Ada")
    return ctx, page, errors


def review(tmp_path, task_kind="reference", **kwargs):
    b = html_bundle(task_kind=task_kind)
    atoms, asset = html_sidecars(b)
    return b, open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                          presentation_mode=ATOMIC, **kwargs)


def box(page, selector):
    return page.locator(selector).bounding_box()


def test_geometry_statements_above_rows_left_viewer_right_and_text_unchanged(tmp_path, browser):
    b, h = review(tmp_path)
    with h:
        ctx, page, errors = opened(browser, h, viewport={"width": 1400, "height": 900})
        statements, rows, viewer = box(page, '[aria-label="Report"]'), box(page, '[aria-label="Judgments"]'), \
            box(page, '[aria-label="Evidence"]')
        assert statements["y"] + statements["height"] <= rows["y"] and statements["y"] + statements["height"] <= viewer["y"]
        assert rows["x"] + rows["width"] <= viewer["x"]
        expect(page.locator("#atoms")).to_be_visible()
        expect(page.locator("#viewer")).to_be_visible()
        # The upper pane shows the saved text exactly; atoms are marks over it, not edits.
        assert page.locator("#report .report-text").inner_text() == STATEMENT
        served = page.evaluate("fetch('/api/bundle', {headers: {Authorization: 'Bearer ' + sessionStorage.getItem('review-token')}}).then(r => r.json())")
        assert validate_bundle(served).bundle_hash == b.bundle_hash
        rows_text = page.locator("[data-atom-row]").all_inner_texts()
        assert len(rows_text) == 4  # 24.6%, 180 bps, 1,150 and one declared fact; FY2026 is an identifier.
        assert any(FACT in t for t in rows_text)
        assert not errors
        ctx.close()


def test_link_and_expand_never_check_and_checks_save_reload_and_clear(tmp_path, browser):
    b, h = review(tmp_path)
    with h:
        ctx, page, _ = opened(browser, h)
        row = page.locator("[data-atom-row]").filter(has_text="“24.6%”")
        check = row.locator("input[type=checkbox]")
        link = row.get_by_role("button", name="Open the source")
        link.click()
        expect(page.locator("#viewer .target-hit")).to_have_count(1)
        # Focus went to the target; Escape returns to the originating row link.
        assert page.evaluate("document.activeElement.classList.contains('target-hit')")
        page.keyboard.press("Escape")
        expect(link).to_be_focused()
        row.locator("summary").click()
        expect(check).not_to_be_checked()
        expect(page.locator("#status")).to_contain_text("revision 0")  # Navigation saved nothing.
        with page.expect_response(lambda r: r.url.endswith("/api/save")) as saved:
            check.check()
        assert saved.value.ok
        expect(page.locator("#status")).to_contain_text("revision 1")
        page.reload()
        row = page.locator("[data-atom-row]").filter(has_text="“24.6%”")
        expect(row.locator("input[type=checkbox]")).to_be_checked()
        with page.expect_response(lambda r: r.url.endswith("/api/save")):
            row.locator("input[type=checkbox]").uncheck()
        page.reload()
        expect(page.locator("[data-atom-row]").filter(has_text="“24.6%”").locator("input[type=checkbox]")).not_to_be_checked()
        ctx.close()


def test_calculated_row_expands_formula_inputs_units_periods_and_sources(tmp_path, browser):
    b, h = review(tmp_path)
    with h:
        ctx, page, _ = opened(browser, h)
        row = page.locator("[data-atom-row]").filter(has_text="“180 bps”")
        expect(row).to_contain_text("Calculated from inputs")
        row.locator("summary").first.click()
        for text in ("(24.6 - 22.8) * 100", "reported 180 bps", "FY2025", "pct", "Input 1:", "Input 2:"):
            expect(row).to_contain_text(text)
        row.get_by_role("button", name="Open input 2 source").click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("22.8%")
        expect(row.locator("input[type=checkbox]")).not_to_be_checked()
        ctx.close()


def test_verified_needs_every_check_but_explained_negative_submits_unchecked(tmp_path, browser):
    b, h = review(tmp_path)
    with h:
        ctx, page, _ = opened(browser, h)
        page.locator("#field-verdict\\:margin").select_option("verified")
        page.locator("#field-report_complete").check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as refused:
            page.locator("#submit").click()
        assert refused.value.status == 400 and "quantity or atom" in refused.value.text()
        for row in page.locator("[data-atom-row] input[type=checkbox]").all():
            with page.expect_response(lambda r: r.url.endswith("/api/save")):
                row.check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as accepted:
            page.locator("#submit").click()
        assert accepted.value.ok, accepted.value.text()
        ctx.close()
    b, h = review(tmp_path / "negative")
    with h:
        ctx, page, _ = opened(browser, h)
        page.locator("#field-verdict\\:margin").select_option("cannot_verify")
        page.get_by_label("Explanation: Source verdict").fill("Outlook sentence has no eligible source.")
        page.locator("#field-report_complete").check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as accepted:
            page.locator("#submit").click()
        assert accepted.value.ok, accepted.value.text()
        assert all(not c.is_checked() for c in page.locator("[data-atom-row] input[type=checkbox]").all())
        ctx.close()


def test_independent_atomic_view_adds_no_completion_gate(tmp_path, browser):
    b, h = review(tmp_path, task_kind="independent", required_fields_only=True)
    with h:
        ctx, page, _ = opened(browser, h)
        page.locator("#field-verdict\\:margin").select_option("cannot_verify")
        page.get_by_label("Explanation: Source verdict").fill("Synthetic")
        page.locator("#field-report_complete").check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as accepted:
            page.locator("#submit").click()
        assert accepted.value.ok, accepted.value.text()
        ctx.close()


def test_keyboard_dividers_zoom_and_denied_storage_keep_controls_and_answers(tmp_path, browser):
    b, h = review(tmp_path)
    with h:
        ctx, page, _ = opened(browser, h)
        check = page.locator("[data-atom-row]").first.locator("input[type=checkbox]")
        with page.expect_response(lambda r: r.url.endswith("/api/save")):
            check.check()
        before = page.locator("#status").inner_text()
        width = page.locator("#pane-width-divider")
        start = box(page, '[aria-label="Judgments"]')["width"]
        width.focus()
        for _ in range(5):
            page.keyboard.press("Shift+ArrowRight")
        assert box(page, '[aria-label="Judgments"]')["width"] > start
        page.locator("#pane-height-divider").focus()
        page.keyboard.press("ArrowDown")
        assert page.locator("#status").inner_text() == before  # Resizing is never a save.
        expect(check).to_be_checked()
        ctx.close()
        # 200% zoom on a 1280x800 screen is a 640x400 CSS viewport; narrow layout stacks the panes.
        ctx, page, _ = opened(browser, h, viewport={"width": 640, "height": 400}, device_scale_factor=2)
        for selector in ("#report .report-text", "[data-atom-row] input[type=checkbox]", ".atom-link", "#submit",
                         "#field-verdict\\:margin"):
            target = page.locator(selector).first
            target.scroll_into_view_if_needed()
            expect(target).to_be_visible()
        page.locator(".atom-link").first.click()
        expect(page.locator("#viewer .target-hit")).to_be_in_viewport()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        ctx.close()
        ctx = browser.new_context()
        ctx.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('denied', 'SecurityError'); }});")
        page = ctx.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 1")
        expect(page.locator("header")).to_contain_text("Pane-size preference unavailable")
        expect(page.locator("[data-atom-row]").first.locator("input[type=checkbox]")).to_be_checked()
        page.locator("#pane-width-divider").focus()
        page.keyboard.press("ArrowLeft")
        expect(page.locator("header")).to_contain_text("could not save the layout")
        ctx.close()
