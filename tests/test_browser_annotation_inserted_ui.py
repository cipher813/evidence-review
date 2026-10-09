"""Real Chromium: answer annotation over an answer whose host also holds inserted page UI
(alpha-engine-config-I12179). Synthetic data only.

An answer host renders original text plus presentation the page inserts into it: number
links/buttons (whose text IS original), quantity checkboxes, "Preview evidence" buttons, inline
calculation cards and their technical diagnostics. A selection binds original text only:

* an endpoint inside inserted UI is refused and nothing is selected or saved;
* original text after an open card keeps its true offsets;
* crossing rule: inserted UI inside a selection is skipped, and the selection is accepted only
  when the original text it covers is one contiguous run of the field; any gap or text the page
  cannot attribute to the answer is refused, never guessed;
* the covered original text must equal the frozen substring the offsets name.
"""
import json
from copy import deepcopy

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, export_json, open_review
from evidence_review.contracts import FormField, ReviewBundle
from synthetic import build, located
from test_browser_answer_annotations import POINTS, disposition, drag_select, saved, settle
from test_diagnostics import diagnostics
from test_reviewer_worksheet import CHOICES, worksheet_bundle

BASE = "https://github.com/example/synthetic/blob/" + "a" * 40 + "/filing.md?plain=1"
VIEWPORT = {"width": 1600, "height": 2400}  # an open card with expanded diagnostics fits on screen
LINKS = {"filing": {"url": BASE, "line_url": BASE + "#L{start}-L{end}", "label": "Frozen original"}}
# The issue's fixture, verbatim (I12179): a calculation on "180 bps".
SUMMARY = "Operating margin rose 180 bps to 24.6%. Revenue fell 4.2%."
# Same calculation, then an astral digit, a decomposed accent and one sentence said twice.
OUTLOOK = "Margin 180 bps held in Region \U0001d7d9; cafe\u0301 sales held. Demand was stable. Demand was stable."
PHRASE = "Demand was stable."
REPEAT = OUTLOOK.index(PHRASE, OUTLOOK.index(PHRASE) + 1)
CAFE_END = OUTLOOK.index("\u0301") + 1


def inserted_ui_bundle():
    """worksheet_bundle's summary plus an outlook field, with diagnostics on the calculation,
    a quantity checkbox on 24.6% and (with LINKS) a direct link + Preview button on 24.6%."""
    base = worksheet_bundle()
    margin = next(c for c in base.claims if c.claim_id == "margin")
    revenue = next(c for c in base.claims if c.claim_id == "revenue")
    current = located(6, "24.6%")
    b = build(
        [("summary", SUMMARY, ["margin", "revenue"]), ("outlook", OUTLOOK, ["margin"])],
        [margin, revenue],
        {("summary", "180 bps"): ("derived", ["margin"]), ("summary", "24.6%"): ("cited", ["margin"]),
         ("summary", "4.2%"): ("cited", ["revenue"]), ("outlook", "180 bps"): ("derived", ["margin"])},
        form=[FormField(field_id="support:revenue", label="Revenue support", options=CHOICES, subject_id="revenue"),
              FormField(field_id="report_complete", label="I reviewed the full report.", kind="boolean",
                        require_true=True)])
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        if s["text"] == "180 bps":
            s["calculation"] = margin.calculation.model_dump(mode="json")
            s["diagnostics"] = diagnostics()
            s["prepared_evidence"] = [{"calculation": deepcopy(s["calculation"]), "reason": "Prepared exact input rows"}]
        elif s["text"] == "24.6%":
            s["citations"] = [current.model_dump(mode="json")]
            data["form"].insert(1, FormField(field_id="qty:current", label="Current margin checked", kind="boolean",
                                             required=False, subject_id="margin",
                                             numeric_span_id=s["span_id"]).model_dump(mode="json"))
    return ReviewBundle.model_validate(data)


# Mouse points around the first RENDERED occurrence of ``needle`` inside one text node under ``root``.
TEXT_POINTS = """([root, needle]) => {
  const scope = document.querySelector(root);
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const at = t.data.indexOf(needle);
    if (at < 0) continue;
    const box = (i) => { const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + 1); return r.getBoundingClientRect(); };
    const a = box(at), b = box(at + needle.length - 1);
    if (!a.width || !b.width) continue; // inside a closed <details>: not rendered
    return {x0: a.left + 1, y0: a.top + a.height / 2, x1: b.right - 1, y1: b.top + b.height / 2};
  }
  throw Error("needle not rendered: " + needle);
}"""
# The host's original answer text: every text node outside inserted UI, in DOM order.
HOST_TEXT = """(path) => {
  const host = document.querySelector(`#report [data-field-path="${path}"]`);
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, {acceptNode: (n) =>
    n.parentElement.closest("[data-not-answer]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  let out = ""; for (let t = walker.nextNode(); t; t = walker.nextNode()) out += t.data; return out;
}"""


def show(page, path):
    """Scroll the answer host to the top of its pane (an open card with diagnostics is tall) and
    clear any selection, so the next mouse drag selects rather than dragging selected text."""
    page.locator(f'#report [data-field-path="{path}"]').evaluate(
        "n => { n.scrollIntoView({block: 'start'}); document.getSelection().removeAllRanges(); }")


def drag(page, a, b):
    page.mouse.move(a["x0"], a["y0"])
    page.mouse.down()
    page.mouse.move(b["x1"], b["y1"], steps=8)
    page.mouse.up()


def drag_text(page, root, needle):
    show(page, "summary")
    p = page.evaluate(TEXT_POINTS, [root, needle])
    drag(page, p, p)


def open_card(page, path):
    page.locator(f'#report [data-field-path="{path}"] button.number', has_text="180 bps").click()
    card = page.get_by_role("region", name="Calculation details", exact=True)
    card.get_by_text("Technical diagnostics", exact=True).first.click()
    expect(card).to_contain_text("Independent preparation: resolved")
    return card


def refused(page, message):
    selected = page.get_by_label("Selected answer text", exact=True)
    expect(selected).to_have_text(message)
    page.get_by_role("button", name="Annotate selected answer text", exact=True).click()
    expect(page.locator("#status")).to_contain_text("Select exact text in an answer field first.")


def bind(page, path, start, end, value, text, crossed=False):
    """Drag original text [start, end) of a field, check the bound range, annotate it."""
    show(page, path)
    drag_select(page, path, start, end)
    selected = page.get_by_label("Selected answer text", exact=True)
    expect(selected).to_contain_text(f"characters {start + 1}–{end}): “{text}”")
    if crossed:
        expect(selected).to_contain_text("calculation details and controls inside the selection are not part of it")
    else:
        expect(selected).not_to_contain_text("not part of it")
    page.get_by_role("button", name="Annotate selected answer text", exact=True).click()
    card = page.locator("#annotations .annotation").last
    disposition(card, value).check()
    if value != "supported":
        card.get_by_label("Annotation reason").fill("Synthetic reason.")
    if value in ("defective", "incomplete"):
        card.get_by_role("radiogroup", name="Annotation materiality").get_by_role(
            "radio", name="Nonmaterial", exact=True).check()
    return card


START_IN_UI = ("That selection starts or ends inside calculation details or page controls, not the answer text. "
               "Nothing was selected.")


def test_inserted_ui_is_never_answer_text_and_original_text_binds_exactly(tmp_path):
    b = inserted_ui_bundle()
    summary_card = '#report [data-field-path="summary"] .calculation-card'
    store = FileStore(tmp_path)
    expected = []
    with open_review(b, store, launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.context.route("https://github.com/**", lambda r: r.fulfill(body="synthetic original"))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        host = page.locator('#report [data-field-path="summary"]')
        # Every insertion site is present inside the answer host and structurally marked.
        expect(host.locator(".quantity-check[data-not-answer] input[type=checkbox]")).to_have_count(1)
        expect(host.locator(".preview-link[data-not-answer]")).to_have_count(1)
        expect(host.locator("a.number", has_text="24.6%")).to_have_count(1)
        card = open_card(page, "summary")
        expect(host.locator(".calculation-card[data-not-answer] .number-diagnostics").first).to_be_visible()
        assert page.evaluate(HOST_TEXT, "summary") == SUMMARY

        # (a) inserted text is refused and saves nothing: the issue's exact case "(24.6 " in the card ...
        formula = card.locator("p", has_text="→ reported").first.inner_text()
        assert "(24.6 " in formula
        drag_text(page, summary_card, "(24.6 ")
        refused(page, START_IN_UI)
        # ... the diagnostics text, a selection starting in the card and ending in original prose,
        drag_text(page, summary_card, "Independent preparation: resolved")
        refused(page, START_IN_UI)
        show(page, "summary")
        drag(page, page.evaluate(TEXT_POINTS, [summary_card, "Formula:"]),
             page.evaluate(POINTS, ["summary", SUMMARY.index("Revenue"), SUMMARY.index("Revenue") + 7]))
        refused(page, START_IN_UI)
        # ... and a selection ending inside the Preview button's label (set by the DOM API: a mouse
        # drag cannot end inside a button's text).
        page.evaluate("""() => {
          const host = document.querySelector('#report [data-field-path="summary"]');
          const first = host.firstChild, label = host.querySelector('.preview-link').firstChild;
          document.getSelection().setBaseAndExtent(first, 0, label, 3);
        }""")
        refused(page, START_IN_UI)
        assert saved(store, b) == []
        # A refusal disarms an earlier valid selection rather than leaving it to be annotated.
        rose = SUMMARY.index("rose")
        show(page, "summary")
        drag_select(page, "summary", rose, rose + 4)
        expect(page.get_by_label("Selected answer text", exact=True)).to_contain_text("“rose”")
        drag_text(page, summary_card, "(24.6 ")
        refused(page, START_IN_UI)
        assert saved(store, b) == []

        # (b) original prose after the open card keeps its true offsets (the issue's second symptom).
        fell = SUMMARY.index("Revenue fell")
        bind(page, "summary", fell, fell + 12, "supported", "Revenue fell")
        expected.append(("summary", fell, fell + 12))
        to24 = SUMMARY.index(" to 24")
        bind(page, "summary", to24 + 1, to24 + 3, "cannot_verify", "to")
        expected.append(("summary", to24 + 1, to24 + 3))

        # (c) a selection crossing the open card and its diagnostics, the number button and link,
        # the checkbox and the Preview button maps to the original text alone.
        bind(page, "summary", rose, fell + 7, "defective", SUMMARY[rose:fell + 7], crossed=True)
        expected.append(("summary", rose, fell + 7))
        settle(page)
        records = saved(store, b)
        assert [(r["answer_range"]["field_path"], r["answer_range"]["start"], r["answer_range"]["end"])
                for r in records] == expected
        assert all(r["answer_range"]["text"] == SUMMARY[r["answer_range"]["start"]:r["answer_range"]["end"]]
                   for r in records)

        # (d) another field: its card open, then the repeated sentence's SECOND occurrence after
        # it, and a crossing selection over the card, the astral number button and a combining mark.
        open_card(page, "outlook")
        expect(page.locator('#report [data-field-path="summary"] .calculation-card')).to_have_count(0)
        bind(page, "outlook", REPEAT, REPEAT + len(PHRASE), "supported", PHRASE)
        expected.append(("outlook", REPEAT, REPEAT + len(PHRASE)))
        bind(page, "outlook", 0, CAFE_END, "incomplete", OUTLOOK[:CAFE_END], crossed=True)
        expected.append(("outlook", 0, CAFE_END))
        for path in ("summary", "outlook"):
            assert page.evaluate(HOST_TEXT, path) == {"summary": SUMMARY, "outlook": OUTLOOK}[path]
        page.get_by_label("Revenue support", exact=True).select_option("supported")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        settle(page)
        browser.close()

    def check(records):
        assert [(r["answer_range"]["field_path"], r["answer_range"]["start"], r["answer_range"]["end"])
                for r in records] == expected
        for r in records:
            text = {"summary": SUMMARY, "outlook": OUTLOOK}[r["answer_range"]["field_path"]]
            assert r["answer_range"]["text"] == text[r["answer_range"]["start"]:r["answer_range"]["end"]]
            assert r["answer_range"]["offset_unit"] == "code_point"

    draft = saved(store, b)
    check(draft)
    assert OUTLOOK[REPEAT:REPEAT + len(PHRASE)] == PHRASE and REPEAT != OUTLOOK.index(PHRASE)
    assert draft[-1]["answer_range"]["text"].endswith("cafe\u0301") and "\U0001d7d9" in draft[-1]["answer_range"]["text"]
    # Restart: a new server process over the same store restores the same occurrences, then submits.
    with open_review(b, FileStore(tmp_path), launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(h.url)
        cards = page.locator("#annotations .annotation")
        expect(cards).to_have_count(len(expected))
        expect(cards.nth(3).get_by_label(
            f"Annotated answer text: Outlook, characters {REPEAT + 1}–{REPEAT + len(PHRASE)}")).to_contain_text(PHRASE)
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Submitted")
        browser.close()
    store = FileStore(tmp_path)
    exported = json.loads(export_json(store, b.bundle_id, store.load_task(b.bundle_id)["last_submission"]))
    check(exported["annotations"])
    assert exported["annotations"] == draft
    assert [f.text for f in b.fields] == [SUMMARY, OUTLOOK]  # candidate text never changes


def test_crossing_a_gap_or_unattributed_text_is_refused_explicitly(tmp_path):
    """The crossing rule's refusals: the page itself is altered here to simulate a rendering
    defect, since a correct rendering never drops answer text or inserts unmarked text."""
    b = inserted_ui_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        open_card(page, "summary")
        host = '#report [data-field-path="summary"]'
        # Unmarked text inserted into the answer host: the page cannot attribute it, so refuse.
        page.evaluate("""() => {
          const card = document.querySelector('#report [data-field-path="summary"] .calculation-card');
          const stray = document.createElement('span'); stray.textContent = ' [unmarked note] ';
          card.after(stray);
        }""")
        show(page, "summary")
        drag(page, page.evaluate(TEXT_POINTS, [host, "rose"]), page.evaluate(TEXT_POINTS, [host, "Revenue"]))
        refused(page, "That selection includes text that is not part of the original answer. Nothing was selected.")
        # Original text missing between the endpoints (" to " removed): not one contiguous run.
        page.evaluate("""() => {
          const host = document.querySelector('#report [data-field-path="summary"]');
          host.querySelectorAll('span').forEach((n) => { if (n.textContent === ' [unmarked note] ') n.remove(); });
          const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
          for (let t = walker.nextNode(); t; t = walker.nextNode()) if (t.data === ' to ') { t.remove(); return; }
          throw Error('no " to " node');
        }""")
        show(page, "summary")
        drag(page, page.evaluate(TEXT_POINTS, [host, "rose"]), page.evaluate(TEXT_POINTS, [host, "Revenue"]))
        refused(page, "The answer text in that selection is not one contiguous passage of the answer, "
                      "so it cannot be bound exactly. Nothing was selected.")
        page.evaluate("() => queue")
        assert saved(store, b) == []
        browser.close()


def test_issue_reproduction_card_text_is_refused_and_prose_after_the_card_binds(tmp_path):
    """The exact I12179 reproduction: worksheet_bundle's summary, its 180 bps card open. On 0.6.0,
    "(24.6 " in the card was bound as Summary characters 30-35 (" to 24") and saved, and
    "Revenue fell" after the card was refused as having no exact answer text."""
    b = worksheet_bundle()
    assert b.fields[0].text == SUMMARY
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.locator('#report [data-field-path="summary"] button.number', has_text="180 bps").click()
        expect(page.get_by_role("region", name="Calculation details", exact=True)).to_be_visible()
        drag_text(page, '#report [data-field-path="summary"] .calculation-card', "(24.6 ")
        refused(page, START_IN_UI)
        expect(page.locator("#annotations .annotation")).to_have_count(0)
        fell = SUMMARY.index("Revenue fell")
        bind(page, "summary", fell, fell + 12, "supported", "Revenue fell")
        settle(page)
        records = saved(store, b)
        assert [(r["answer_range"]["start"], r["answer_range"]["end"], r["answer_range"]["text"]) for r in records] == [
            (40, 52, "Revenue fell")]
        browser.close()
