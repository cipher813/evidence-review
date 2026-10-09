"""Real Chromium: reviewer-selected answer annotations (answer-annotation/v1). Synthetic data only.

Selections are made with the mouse over the rendered answer, not injected, and the
store re-validates every offset the browser computed.
"""
import json

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, export_json, open_review
from evidence_review.contracts import FormField, ReviewBundle, canonical_json
from synthetic import build, margin_claim

SUPPORT = "Does the evidence support the margin claim?"
# Prose outside every claim: an astral digit (a number button), a decomposed accent,
# then one sentence said twice.
QUALIFICATION = "Region 𝟙 note: café sales held. Demand was stable. Demand was stable."
PHRASE = "Demand was stable."
SECOND = QUALIFICATION.index(PHRASE, QUALIFICATION.index(PHRASE) + 1)
FIRST = QUALIFICATION.index(PHRASE)

# Pixel boundaries of code-point offsets in one rendered answer, counting answer text only.
POINTS = """([path, start, end]) => {
  const host = document.querySelector(`#report [data-field-path="${path}"]`);
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, {acceptNode: (n) =>
    n.parentElement.closest("[data-not-answer]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  const where = (cp) => {
    let seen = 0;
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      const chars = Array.from(t.data);
      if (cp < seen + chars.length) return [t, chars.slice(0, cp - seen).join("").length];
      seen += chars.length;
    }
    throw Error("offset beyond the answer");
  };
  const rect = (cp) => {
    walker.currentNode = host;
    const [node, unit] = where(cp);
    const r = document.createRange();
    r.setStart(node, unit); r.setEnd(node, unit + (node.data.codePointAt(unit) > 0xffff ? 2 : 1));
    return r.getBoundingClientRect();
  };
  const a = rect(start), b = rect(end - 1);
  return {x0: a.left + 1, y0: a.top + a.height / 2, x1: b.right - 1, y1: b.top + b.height / 2};
}"""


def annotated_bundle():
    b = build([("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"]),
               ("qualifications", QUALIFICATION, [])],
              [margin_claim()],
              {("summary", "180 bps"): ("derived", ["margin"]), ("summary", "24.6%"): ("cited", ["margin"])},
              form=[FormField(field_id="support:margin", label=SUPPORT, options=["supported", "unsupported"],
                              subject_id="margin"),
                    FormField(field_id="report_complete", label="I reviewed the full report.", kind="boolean",
                              require_true=True)])
    return ReviewBundle.model_validate(b.model_dump(mode="json"))


def drag_select(page, path, start, end):
    p = page.evaluate(POINTS, [path, start, end])
    page.mouse.move(p["x0"], p["y0"])
    page.mouse.down()
    page.mouse.move(p["x1"], p["y1"], steps=8)
    page.mouse.up()


# The visible label of each disposition radio (app.js DISPOSITIONS).
DISPOSITION_LABEL = {"supported": "Supported by the sources", "defective": "Defective",
                     "cannot_verify": "Cannot verify", "incomplete": "Incomplete"}


def disposition(card, value):
    """The radio for one disposition in an annotation card's disposition group."""
    return card.get_by_role("radiogroup", name="Annotation disposition").get_by_role(
        "radio", name=DISPOSITION_LABEL[value], exact=True)


def materiality(card, material):
    return card.get_by_role("radiogroup", name="Annotation materiality").get_by_role(
        "radio", name="Material" if material else "Nonmaterial", exact=True)


def annotate(page, path, start, end, disposition_value, reason=""):
    drag_select(page, path, start, end)
    expect(page.get_by_label("Selected answer text", exact=True)).to_contain_text(
        f"characters {start + 1}–{end}")
    revision = page.locator("#status").text_content()
    page.get_by_role("button", name="Annotate selected answer text").click()
    card = page.locator("#annotations .annotation").last
    expect(page.locator("#status")).not_to_have_text(revision)
    disposition(card, disposition_value).check()
    if reason:
        card.get_by_label("Annotation reason").fill(reason)
    return card


def saved(store, b):
    return store.load_task(b.bundle_id)["answers"].get("annotations", [])


def settle(page):
    """Wait for this tab's save queue to drain, then for its acknowledged status."""
    page.evaluate("() => queue")
    expect(page.locator("#status")).to_contain_text("Saved locally")


def test_mouse_selection_binds_exact_code_points_and_the_chosen_occurrence(tmp_path):
    b = annotated_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        # Second occurrence of a repeated sentence, after an astral digit and a combining mark.
        card = annotate(page, "qualifications", SECOND, SECOND + len(PHRASE), "cannot_verify", "No frozen source covers demand.")
        expect(card.get_by_label("Annotated answer text: Qualifications, characters "
                                 f"{SECOND + 1}–{SECOND + len(PHRASE)}")).to_contain_text(PHRASE)
        expect(card.get_by_role("radiogroup", name="Annotation materiality")).to_be_hidden()
        annotate(page, "qualifications", FIRST, FIRST + len(PHRASE), "supported")
        # A passage starting at the combining mark's base letter and crossing the number button.
        accent = QUALIFICATION.index("cafe")
        annotate(page, "qualifications", 0, accent + 5, "incomplete", "Omits which region.")
        materiality(page.locator("#annotations .annotation").last, False).check()
        settle(page)
        records = saved(store, b)
        ranges = [(r["answer_range"]["start"], r["answer_range"]["end"], r["answer_range"]["text"]) for r in records]
        # Python str offsets are code points; the browser computed the same numbers.
        assert ranges == [(SECOND, SECOND + 18, PHRASE), (FIRST, FIRST + 18, PHRASE),
                          (0, accent + 5, QUALIFICATION[:accent + 5])]
        assert QUALIFICATION[:accent + 5].endswith("café") and "𝟙" in ranges[2][2]
        assert [r["disposition"] for r in records] == ["cannot_verify", "supported", "incomplete"]
        assert records[0]["reason"] == "No frozen source covers demand." and records[1]["reason"] == ""
        assert records[2]["material"] is False and records[0]["material"] is None
        assert all(r["annotation_id"].startswith("ann:") for r in records)
        assert len({r["annotation_id"] for r in records}) == 3
        # A selection crossing two answer fields is refused, not split or guessed.
        start = page.evaluate(POINTS, ["summary", 0, 3])
        end = page.evaluate(POINTS, ["qualifications", 0, 3])
        page.mouse.move(start["x0"], start["y0"]); page.mouse.down()
        page.mouse.move(end["x1"], end["y1"], steps=8); page.mouse.up()
        expect(page.get_by_label("Selected answer text", exact=True)).to_have_text("Select text within one answer field.")
        page.get_by_role("button", name="Annotate selected answer text").click()
        expect(page.locator("#status")).to_contain_text("Select exact text in an answer field first.")
        assert len(saved(store, b)) == 3
        assert b.fields[1].text == QUALIFICATION  # original answer text unchanged
        browser.close()


def test_draft_restart_submit_amend_export_and_defect_ranges(tmp_path):
    b = annotated_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        annotate(page, "qualifications", SECOND, SECOND + len(PHRASE), "cannot_verify", "Unverifiable demand.")
        # A defect bound to an exact passage, and an omission defect without any answer text.
        page.get_by_role("button", name="Add defect").click()
        drag_select(page, "qualifications", FIRST, FIRST + len(PHRASE))
        page.locator(".defect").first.get_by_role("button", name="Attach selected answer text to defect").click()
        page.get_by_role("button", name="Add defect").click()
        omission = page.locator(".defect").nth(1)
        expect(omission).to_contain_text("No answer passage attached: this records an omission")
        for i, (category, note) in enumerate([("unsupported", "Demand claim lacks support."),
                                              ("omission", "Omits the disposed segment.")]):
            d = page.locator(".defect").nth(i)
            d.get_by_label("Defect category").fill(category)
            d.get_by_label("Defect materiality").select_option("true")
            d.get_by_label("Defect evidence explanation").fill(note)
        page.get_by_label(SUPPORT, exact=True).select_option("supported")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        settle(page)
        browser.close()
    # Restart: a new server process over the same durable state restores the draft.
    store = FileStore(tmp_path)
    draft = store.load_task(b.bundle_id)
    assert draft["answers"]["annotations"][0]["reason"] == "Unverifiable demand."
    assert draft["answers"]["defects"][0]["answer_ranges"][0]["start"] == FIRST
    assert "answer_ranges" not in draft["answers"]["defects"][1]
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        card = page.locator("#annotations .annotation").first
        expect(disposition(card, "cannot_verify")).to_be_checked()
        expect(card.get_by_label("Annotation reason")).to_have_value("Unverifiable demand.")
        expect(page.locator(".defect").first.get_by_label(
            f"Annotated answer text: Qualifications, characters {FIRST + 1}–{FIRST + len(PHRASE)}")).to_be_visible()
        page.get_by_role("button", name="Submit review").click()
        expect(page.locator("#status")).to_contain_text("Submitted")
        first = store.load_task(b.bundle_id)["last_submission"]
        # Amend the annotation: the earlier submission keeps its original reason.
        card.get_by_label("Annotation reason").fill("Unverifiable demand; no period stated.")
        page.get_by_label("Amendment reason").fill("clarified annotation")
        page.get_by_role("button", name="Submit review").click()
        expect(page.locator("#status")).to_contain_text("Submitted")
        expect(page.locator("#status")).not_to_contain_text(f"revision {first} ")
        browser.close()
    last = store.load_task(b.bundle_id)["last_submission"]
    before, after = (json.loads(export_json(store, b.bundle_id, r)) for r in (first, last))
    assert before["annotations"][0]["reason"] == "Unverifiable demand."
    assert after["annotations"][0]["reason"] == "Unverifiable demand; no period stated."
    assert after["annotations"][0]["annotation_id"] == before["annotations"][0]["annotation_id"]
    assert after["provenance"]["amends_revision"] == str(first)
    assert after["defects"][0]["answer_ranges"] == before["defects"][0]["answer_ranges"]
    assert export_json(store, b.bundle_id, last) == canonical_json(after)


def test_stale_tab_reports_differing_annotations_without_overwrite(tmp_path):
    b = annotated_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        first, second = (browser.new_page(viewport={"width": 1600, "height": 1000}) for _ in range(2))
        for page in (first, second):
            page.goto(h.url)
            expect(page.locator("#status")).to_contain_text("revision 0")
            page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        annotate(first, "qualifications", FIRST, FIRST + len(PHRASE), "supported")
        settle(first)
        drag_select(second, "qualifications", SECOND, SECOND + len(PHRASE))
        second.get_by_role("button", name="Annotate selected answer text").click()
        conflict = second.get_by_role("alert")
        expect(conflict).to_contain_text("Differs from saved: Answer annotations")
        assert [a["answer_range"]["start"] for a in saved(store, b)] == [FIRST]
        second.get_by_role("button", name="Use saved version").click()
        expect(second.locator("#annotations .annotation")).to_have_count(1)
        expect(disposition(second.locator("#annotations .annotation").first, "supported")).to_be_checked()
        browser.close()
