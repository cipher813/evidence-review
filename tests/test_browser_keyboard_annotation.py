"""Real Chromium, keyboard only: choose exact answer text by typing it and picking its occurrence.

No mouse event is sent. The run starts from one focusable start point (the Assessor box) and
every later step is a key press; the stored offsets are the same code points Python computes.

The answer field, occurrence, disposition and materiality choices are radio groups. They were
native <select>s until 0.6.0, and ArrowDown on a closed native select is platform-dependent: on
some platforms it opens the popup instead of changing the value, and in at least one Chromium
run (alpha-engine-config-I12006) it changed nothing at all while Tab worked. A radio group's
Tab-in / arrow-between / Space-to-check behaviour is implemented by the browser engine itself
and is the same on every platform, so this path no longer depends on a native widget.
"""
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from test_browser_answer_annotations import (FIRST, PHRASE, QUALIFICATION, SECOND, annotated_bundle, disposition,
                                             saved, settle)

# From the astral digit through the decomposed accent (e + U+0301).
ASTRAL = QUALIFICATION.index("𝟙")
ACCENT_END = QUALIFICATION.index("́") + 1


def keys(page, *presses):
    for press in presses:
        page.keyboard.press(press)


def tab_to(page, target, limit=40):
    """Press Tab until ``target`` has focus; how many presses that takes is page structure, not behaviour."""
    for _ in range(limit):
        if target.evaluate("n => n === document.activeElement"):
            return
        page.keyboard.press("Tab")
    expect(target).to_be_focused()


def test_keyboard_only_annotation_binds_the_chosen_occurrence_in_code_points(tmp_path):
    b = annotated_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).focus()  # the one start point
        page.keyboard.type("Synthetic reviewer")

        fields = page.get_by_role("radiogroup", name="Answer field to annotate", exact=True)
        summary = fields.get_by_role("radio", name="Summary", exact=True)
        qualifications = fields.get_by_role("radio", name="Qualifications", exact=True)
        occurrence = page.get_by_role("radiogroup", name="Occurrence of the typed text", exact=True)
        text = page.get_by_label("Exact answer text to annotate", exact=True)
        selected = page.get_by_label("Selected answer text", exact=True)
        use = page.get_by_role("button", name="Select this occurrence", exact=True)
        annotate = page.get_by_role("button", name="Annotate selected answer text", exact=True)

        # Tab enters the field group at its checked radio; an arrow key moves and checks.
        tab_to(page, summary)
        expect(summary).to_be_checked()
        keys(page, "ArrowDown")  # Summary -> Qualifications
        expect(qualifications).to_be_focused()
        expect(qualifications).to_be_checked()
        keys(page, "Tab")
        expect(text).to_be_focused()
        page.keyboard.type(PHRASE)
        options = occurrence.get_by_role("radio")
        expect(options).to_have_count(2)
        expect(occurrence.locator("label").nth(1)).to_have_text(
            f"Occurrence 2 of 2 (characters {SECOND + 1}–{SECOND + len(PHRASE)}): "
            f"…{QUALIFICATION[SECOND - 20:SECOND]}[{PHRASE}]…")
        keys(page, "Tab")
        expect(options.nth(0)).to_be_focused()
        expect(options.nth(0)).to_be_checked()
        keys(page, "ArrowDown")  # the second, repeated occurrence
        expect(options.nth(1)).to_be_focused()
        expect(options.nth(1)).to_be_checked()
        assert options.nth(1).get_attribute("value") == f"{SECOND}:{SECOND + len(PHRASE)}"
        keys(page, "Tab")
        expect(use).to_be_focused()
        keys(page, "Enter")
        expect(selected).to_contain_text(f"Qualifications, characters {SECOND + 1}–{SECOND + len(PHRASE)}")
        keys(page, "Tab")
        expect(annotate).to_be_focused()
        keys(page, "Enter")
        card = page.locator("#annotations .annotation").first
        # Focus moves to the new card's first disposition radio, unchecked; arrows move and check.
        expect(disposition(card, "supported")).to_be_focused()
        expect(disposition(card, "supported")).not_to_be_checked()
        keys(page, "ArrowDown", "ArrowDown")  # Defective, then Cannot verify
        expect(disposition(card, "cannot_verify")).to_be_checked()
        keys(page, "Tab")  # materiality is hidden for cannot verify
        expect(card.get_by_label("Annotation reason")).to_be_focused()
        page.keyboard.type("No frozen source covers demand.")

        # Text crossing an astral digit and a combining mark; one occurrence, chosen by default.
        tab_to(page, qualifications)  # forward through the card's controls into the field group
        expect(qualifications).to_be_checked()
        keys(page, "Tab")
        expect(text).to_be_focused()
        keys(page, "Control+a", "Delete")
        page.keyboard.type(QUALIFICATION[ASTRAL:ACCENT_END])
        expect(occurrence.get_by_role("radio")).to_have_count(1)
        keys(page, "Tab")
        expect(occurrence.get_by_role("radio").first).to_be_checked()
        keys(page, "Tab", "Enter", "Tab", "Enter")
        second = page.locator("#annotations .annotation").nth(1)
        expect(disposition(second, "supported")).to_be_focused()
        keys(page, "Space")  # check the focused radio
        expect(disposition(second, "supported")).to_be_checked()

        # Text that is not in the field selects nothing and annotates nothing new.
        settle(page)  # no save may overwrite the refusal below
        tab_to(page, qualifications)
        keys(page, "Tab")
        expect(text).to_be_focused()
        keys(page, "Control+a", "Delete")
        page.keyboard.type("Demand was falling.")
        expect(occurrence).to_have_text("No exact occurrence in this field")
        expect(occurrence.get_by_role("radio")).to_have_count(0)
        keys(page, "Tab")  # no occurrence to choose: straight to the button
        expect(use).to_be_focused()
        keys(page, "Enter")
        expect(selected).to_have_text("No occurrence chosen: type text that occurs exactly in the chosen answer field.")
        keys(page, "Tab", "Enter")
        expect(page.locator("#status")).to_contain_text("Select exact text in an answer field first.")
        expect(page.locator("#annotations .annotation")).to_have_count(2)

        page.evaluate("() => queue")  # nothing new was queued; the refusal stays shown
        records = saved(store, b)
        assert [(r["answer_range"]["start"], r["answer_range"]["end"], r["answer_range"]["text"]) for r in records] == [
            (SECOND, SECOND + len(PHRASE), PHRASE), (ASTRAL, ACCENT_END, QUALIFICATION[ASTRAL:ACCENT_END])]
        assert ASTRAL == 7 and SECOND != FIRST  # code points, not UTF-16 units
        assert [r["disposition"] for r in records] == ["cannot_verify", "supported"]
        assert records[0]["reason"] == "No frozen source covers demand."
        assert b.fields[1].text == QUALIFICATION
        browser.close()
