"""Guided review on a real-shaped bundle: one statement, its evidence and its decision together."""

from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, FormField, ReferenceItem, ReportField, ReviewBundle, digest
from evidence_review.evidence import numeric_spans
from synthetic import located, source, unavailable

SUPPORT = ["supported", "partially supported", "unsupported", "contradicted", "cannot determine"]
COVERAGE = ["addressed", "partially addressed", "missing", "not_assessable"]
MEANING = {
    "supported": "The cited evidence states what the claim says.",
    "cannot determine": "The evidence needed is missing or unreadable.",
}


def guided_bundle():
    claims = [
        Claim(claim_id=f"C{i}", text=f"Statement {i}: operating margin was 24.6% in segment {chr(64 + i)}.",
              citations=[located(6, "24.6%")] if i != 3 else [unavailable()])
        for i in range(1, 15)
    ]
    prose = [Claim(claim_id=f"prose:summary.{i}", text=f"Summary sentence {i} restates the margin.") for i in range(8)]
    refs = [ReferenceItem(reference_id=f"R{i}", text=f"Reference point {i}: revenue fell to 1,150.", citations=[located(7, "1,150")])
            for i in range(1, 7)]
    fields = [ReportField(path="context.question", label="Research question",
                          text="Did the 18% margin target hold through FY2026?", role="context")]
    fields += [ReportField(path=f"claims.{c.claim_id}", label=f"Claim {c.claim_id}", text=c.text, claim_ids=[c.claim_id])
               for c in claims]
    fields += [ReportField(path=f"summary.{i}", label="Summary", text=c.text, claim_ids=[c.claim_id]) for i, c in enumerate(prose)]
    spans = []
    for f in fields:
        for n in numeric_spans(f.path, f.text):
            if f.role == "context":
                n.state, n.reason = "identifier", "task context"
            elif n.state != "identifier":
                n.state, n.claim_ids = "cited", list(f.claim_ids)
            spans.append(n)
    form = [FormField(field_id="support:" + c.claim_id, label="Evidence support: " + c.claim_id, options=SUPPORT,
                      subject_id=c.claim_id, note_required_unless=["supported"],
                      help="Does the cited evidence support this statement as written?", option_help=MEANING)
            for c in claims + prose]
    form += [FormField(field_id="coverage:" + r.reference_id, label="Coverage: " + r.reference_id, options=COVERAGE,
                       subject_id=r.reference_id, note_required_unless=["addressed", "not_assessable"]) for r in refs]
    form += [FormField(field_id="assessment_status", label="Is the full answer assessable?", options=["complete", "not_assessable"]),
             FormField(field_id="report_complete", label="Full review complete", kind="boolean", require_true=True)]
    return ReviewBundle(bundle_id="guided", document_hashes={"report": digest([f.model_dump() for f in fields])},
                        instructions="Judge each statement against its evidence, then say which reference points the answer covers.",
                        fields=fields, sources=[source()], spans=spans, claims=claims + prose, references=refs, form=form)


def test_reviewer_works_one_item_at_a_time_and_reaches_the_next_unanswered(tmp_path):
    b = guided_bundle()
    assert len([f for f in b.form if f.required]) >= 30 and len(b.claims) >= 12
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        page = pw.chromium.launch().new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        # Question and answer are separate, and the question's numbers are not candidate assertions.
        context = page.get_by_label("Task context")
        expect(context).to_contain_text("Did the 18% margin target hold")
        expect(context.get_by_role("button")).to_have_count(0)
        expect(page.get_by_label("What to do")).to_contain_text("Judge each statement")
        # The first item is current: its full text, its own control, the option meanings, and its evidence.
        item = page.get_by_label("Current item")
        expect(item).to_contain_text("Item 1 of 28: Evidence support: C1")
        expect(item).to_contain_text("Statement 1: operating margin was 24.6% in segment A.")
        expect(item).to_contain_text("The cited evidence states what the claim says.")
        expect(item.get_by_role("checkbox")).to_have_count(0)  # no claim-ID linking for a single-subject judgment
        page.locator("#evidence-panel > summary").click()
        expect(page.get_by_role("region", name="Evidence")).to_contain_text("L6 (cited)")
        # Keyboard: choose, then move to the next unanswered item.
        control = page.get_by_label("Evidence support: C1", exact=True)
        control.focus()
        control.select_option("supported")
        expect(page.locator("#status")).to_contain_text("1/30 fields answered")
        page.locator("body").click(position={"x": 1, "y": 1})
        page.keyboard.press("n")
        expect(item).to_contain_text("Item 2 of 28: Evidence support: C2")
        expect(page.get_by_label("Evidence support: C2", exact=True)).to_be_focused()
        page.keyboard.press("Tab")
        page.keyboard.press("p")  # typing in the note does not navigate
        expect(item).to_contain_text("Item 2 of 28")
        # A statement with an unavailable source says so instead of implying support.
        page.get_by_role("button", name="3. Evidence support: C3 · unanswered").click()
        expect(page.get_by_role("region", name="Evidence")).to_contain_text("operand not found in frozen sources")
        # Opening source evidence preserves the current judgment.
        page.get_by_role("region", name="Report").get_by_role("button", name="24.6%, cited").nth(4).click()
        expect(item).to_contain_text("Evidence support: C3")
        # Coverage can link several claims, shown by their text.
        page.get_by_role("button", name="23. Coverage: R1 · unanswered").click()
        expect(item).to_contain_text("Reference point 1: revenue fell to 1,150.")
        item.get_by_label("Coverage: R1", exact=True).select_option("partially addressed")
        item.get_by_label("Explanation: Coverage: R1", exact=True).fill("Synthetic partial coverage requires explanation.")
        item.get_by_role("checkbox", name="C1: Statement 1").check()
        item.get_by_role("checkbox", name="C2: Statement 2").check()
        expect(page.get_by_label("Review progress")).to_contain_text("2 of 30 required fields complete")
    saved = store.load_task(b.bundle_id)["answers"]["judgments"]
    assert saved["support:C1"]["value"] == "supported"
    assert saved["coverage:R1"]["claim_ids"] == ["C1", "C2"]
    assert saved["support:C1"]["claim_ids"] == []
