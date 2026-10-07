"""Layout preferences, dialogs and long content never block review or change saved answers."""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import validate_bundle
from evidence_review.example import example_bundle
from test_reference_layout import reference_quantity_bundle

STORAGE_KEY = "evidence-review-pane-sizes/v1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def start(browser, h, init=None, **context):
    ctx = browser.new_context(**context)
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    return ctx, page, errors


@pytest.mark.parametrize("stored,message", [
    ("{not json", "Pane-size preference unavailable"),
    ('{"left": 9999, "top": -50}', None),
    ('{"left": "wide", "top": null}', None),
])
def test_corrupt_or_out_of_range_preferences_fall_back_safely(tmp_path, browser, stored, message):
    with open_review(reference_quantity_bundle(), FileStore(tmp_path), launch=False, assessor="Ada") as h:
        init = f"try {{ localStorage.setItem({STORAGE_KEY!r}, {stored!r}); }} catch (e) {{}}"
        ctx, page, errors = start(browser, h, init=init, viewport={"width": 1440, "height": 1000})
        divider = page.get_by_role("separator", name="Resize left pane width")
        now = float(divider.get_attribute("aria-valuenow"))
        assert float(divider.get_attribute("aria-valuemin")) <= now <= float(divider.get_attribute("aria-valuemax"))
        if message:
            expect(page.locator("header")).to_contain_text(message)
        expect(page.locator("#items select").first).to_be_visible()
        assert not errors
        ctx.close()


def test_double_click_restores_defaults_and_resizing_never_saves(tmp_path, browser):
    with open_review(reference_quantity_bundle(), FileStore(tmp_path), launch=False, assessor="Ada") as h:
        ctx, page, _ = start(browser, h, viewport={"width": 1440, "height": 1000})
        width = page.get_by_role("separator", name="Resize left pane width")
        height = page.get_by_role("separator", name="Resize evidence height")
        width.focus()
        page.keyboard.press("Shift+ArrowRight")
        height.focus()
        page.keyboard.press("Shift+ArrowDown")
        assert width.get_attribute("aria-valuenow") != "52" and height.get_attribute("aria-valuenow") != "35"
        width.dblclick()
        height.dblclick()
        assert (width.get_attribute("aria-valuenow"), height.get_attribute("aria-valuenow")) == ("52", "35")
        expect(page.locator("#status")).to_contain_text("revision 0")
        ctx.close()


def test_review_guide_dialog_traps_focus_and_returns_it(tmp_path, browser):
    with open_review(example_bundle(), FileStore(tmp_path), launch=False) as h:
        ctx, page, _ = start(browser, h)
        opener = page.locator("#open-guide")
        opener.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#review-guide")).to_be_visible()
        assert page.evaluate("document.querySelector('#review-guide').contains(document.activeElement)")
        page.keyboard.press("Escape")
        expect(page.locator("#review-guide")).to_be_hidden()
        expect(opener).to_be_focused()
        page.locator("#open-resources").click()
        page.get_by_role("button", name="Close sources and rubric").click()
        expect(page.locator("#open-resources")).to_be_focused()
        ctx.close()


def long_bundle():
    data = example_bundle().model_dump(mode="json")
    answer = next(f for f in data["fields"] if f["role"] == "answer")
    filler = " ".join(f"Qualitative sentence {i} restates context without new figures." for i in range(400))
    answer["text"] = answer["text"] + "\n\n" + filler.replace("0", "zero").replace("1", "one").replace(
        "2", "two").replace("3", "three").replace("4", "four").replace("5", "five").replace("6", "six").replace(
        "7", "seven").replace("8", "eight").replace("9", "nine")
    from evidence_review.contracts import digest
    from evidence_review.evidence import numeric_spans
    old = {(s["field_path"], s["start"], s["end"]): s for s in data["spans"]}
    data["spans"] = [{**n.model_dump(mode="json"), **{k: v for k, v in old.get((n.field_path, n.start, n.end), {}).items()
                                                      if k not in ("start", "end", "text", "span_id", "field_path")}}
                     for f in data["fields"] for n in numeric_spans(f["path"], f["text"])]
    data["document_hashes"] = {**data["document_hashes"], "report": digest([f for f in data["fields"]])}
    return validate_bundle(data)


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 640, "height": 400}])
def test_long_answer_keeps_every_control_reachable_and_text_exact(tmp_path, browser, viewport):
    b = long_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        ctx, page, _ = start(browser, h, viewport=viewport, device_scale_factor=2 if viewport["width"] < 700 else 1)
        texts = page.locator("#report .report-text").all_inner_texts()
        answer = next(f for f in b.fields if f.role == "answer")
        assert any(t == answer.text for t in texts)
        for selector in ("#submit", "#add-defect", "#field-report_complete", "#pane-width-divider" if viewport["width"] > 1000 else "#search"):
            target = page.locator(selector)
            if selector == "#search":
                page.locator("#evidence-panel > summary").scroll_into_view_if_needed()
            target.scroll_into_view_if_needed()
            expect(target).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        ctx.close()


def test_answers_survive_resize_reset_reload_with_storage_denied(tmp_path, browser):
    b = reference_quantity_bundle()
    deny = ("Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('denied', "
            "'SecurityError'); }});")
    with open_review(b, FileStore(tmp_path), launch=False, assessor="Ada") as h:
        ctx, page, errors = start(browser, h, init=deny, viewport={"width": 1440, "height": 1000})
        expect(page.locator("header")).to_contain_text("Pane-size preference unavailable")
        check = page.locator("#items input[data-quantity-field]").first
        with page.expect_response(lambda r: r.url.endswith("/api/save")):
            check.check()
        expect(page.locator("#status")).to_contain_text("revision 1")
        width = page.get_by_role("separator", name="Resize left pane width")
        box = width.bounding_box()
        page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 120, box["y"] + box["height"] / 2)
        page.mouse.up()
        expect(page.locator("header")).to_contain_text("could not save the layout")
        width.dblclick()
        page.reload()
        expect(page.locator("#status")).to_contain_text("revision 1")
        expect(page.locator("#items input[data-quantity-field]").first).to_be_checked()
        assert not errors
        ctx.close()
