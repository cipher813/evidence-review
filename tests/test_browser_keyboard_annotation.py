"""Real Chromium, keyboard only: choose exact answer text by typing it and picking its occurrence.

No mouse event is sent. The first control is reached by focus(); every later step is a
key press, and the stored offsets are the same code points Python computes.
"""
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from test_browser_answer_annotations import FIRST, PHRASE, QUALIFICATION, SECOND, annotated_bundle, saved, settle

# From the astral digit through the decomposed accent (e + U+0301).
ASTRAL = QUALIFICATION.index("𝟙")
ACCENT_END = QUALIFICATION.index("́") + 1


def keys(page, *presses):
    for press in presses:
        page.keyboard.press(press)


def test_keyboard_only_annotation_binds_the_chosen_occurrence_in_code_points(tmp_path):
    b = annotated_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("revision 0")
        page.get_by_label("Assessor", exact=True).focus()
        page.keyboard.type("Synthetic reviewer")

        field = page.get_by_label("Answer field to annotate", exact=True)
        occurrence = page.get_by_label("Occurrence of the typed text", exact=True)
        selected = page.get_by_label("Selected answer text", exact=True)
        field.focus()
        expect(field).to_have_value("summary")
        keys(page, "ArrowDown")  # Summary -> Qualifications
        expect(field).to_have_value("qualifications")
        keys(page, "Tab")
        expect(page.get_by_label("Exact answer text to annotate", exact=True)).to_be_focused()
        page.keyboard.type(PHRASE)
        keys(page, "Tab")
        expect(occurrence).to_be_focused()
        expect(occurrence.locator("option")).to_have_count(2)
        expect(occurrence.locator("option").nth(1)).to_have_text(
            f"Occurrence 2 of 2 (characters {SECOND + 1}–{SECOND + len(PHRASE)}): "
            f"…{QUALIFICATION[SECOND - 20:SECOND]}[{PHRASE}]…")
        keys(page, "ArrowDown")  # the second, repeated occurrence
        expect(occurrence).to_have_value(f"{SECOND}:{SECOND + len(PHRASE)}")
        keys(page, "Tab", "Enter")  # Select this occurrence
        expect(selected).to_contain_text(f"Qualifications, characters {SECOND + 1}–{SECOND + len(PHRASE)}")
        keys(page, "Tab")
        expect(page.get_by_role("button", name="Annotate selected answer text")).to_be_focused()
        keys(page, "Enter")
        card = page.locator("#annotations .annotation").first
        # Focus moves to the new card's disposition: cannot verify is the third choice.
        expect(card.get_by_label("Annotation disposition")).to_be_focused()
        keys(page, "ArrowDown", "ArrowDown", "ArrowDown")
        expect(card.get_by_label("Annotation disposition")).to_have_value("cannot_verify")
        keys(page, "Tab")  # materiality is hidden for cannot verify
        expect(card.get_by_label("Annotation reason")).to_be_focused()
        page.keyboard.type("No frozen source covers demand.")

        # Text crossing an astral digit and a combining mark; one occurrence, chosen by default.
        field.focus()
        page.keyboard.press("Tab")
        keys(page, "Control+a", "Delete")
        page.keyboard.type(QUALIFICATION[ASTRAL:ACCENT_END])
        keys(page, "Tab")
        expect(occurrence.locator("option")).to_have_count(1)
        keys(page, "Tab", "Enter", "Tab", "Enter")
        second = page.locator("#annotations .annotation").nth(1)
        expect(second.get_by_label("Annotation disposition")).to_be_focused()
        keys(page, "ArrowDown")
        expect(second.get_by_label("Annotation disposition")).to_have_value("supported")

        # Text that is not in the field selects nothing and annotates nothing new.
        settle(page)  # no save may overwrite the refusal below
        field.focus()
        keys(page, "Tab", "Control+a", "Delete")
        page.keyboard.type("Demand was falling.")
        keys(page, "Tab")
        expect(occurrence.locator("option")).to_have_text(["No exact occurrence in this field"])
        keys(page, "Tab", "Enter")
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
