"""Real Chromium: evidence context, conflict recovery, keyboard use and blinding."""

import json
from playwright.sync_api import sync_playwright, expect
from evidence_review import FileStore, open_review
from evidence_review.contracts import FormField, digest
from evidence_review.example import example_bundle
from synthetic import TABLE, build, margin_claim, source, unavailable

SUPPORT = "Does the evidence support the margin claim?"
CHOICES = ["supported", "partially supported", "unsupported", "contradicted", "cannot determine"]


def margin_bundle(prior=None, text=TABLE):
    b = build(
        [
            ("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"]),
            ("qualifications", "Revenue figures exclude 1 disposed segment.", []),
        ],
        [margin_claim(prior)],
        {("summary", "180 bps"): ("derived", ["margin"]), ("summary", "24.6%"): ("cited", ["margin"])},
        sources=[source(text)],
        form=[
            FormField(field_id="support:margin", label=SUPPORT, options=CHOICES, subject_id="margin", note_required_unless=["supported"]),
            FormField(field_id="report_complete", label="I reviewed the full report.", kind="boolean", require_true=True),
        ],
    )
    for n in b.spans:
        if n.text == "180 bps": n.calculation = b.claims[0].calculation
        elif n.text == "24.6%": n.citations = [b.claims[0].citations[-1]]
    return b.model_validate(b.model_dump(mode="json"))



def launch(pw):
    return pw.chromium.launch()


def test_operand_context_and_missing_prior_stay_unresolved(tmp_path):
    with open_review(margin_bundle(unavailable()), FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        page = launch(pw).new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        report = page.get_by_role("region", name="Report")
        expect(report).to_contain_text("subjects with unresolved evidence: margin")
        page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="180 bps, derived", exact=True).click()
        page.get_by_role("button", name="Preview calculation evidence", exact=True).click()
        evidence = page.get_by_role("region", name="Evidence")
        evidence.get_by_text("Original candidate calculation", exact=True).click()
        expect(evidence).to_contain_text("Arithmetic unresolved: Unresolved input evidence: prior")
        expect(evidence).not_to_contain_text("Recomputed (Decimal)")
        expect(evidence).to_contain_text("Input evidence unresolved: no located citation for prior.")
        expect(evidence).to_contain_text("Candidate citation (prior): operand not found in frozen sources")
        evidence.get_by_role("button", name="Preview input current", exact=True).click()
        expect(evidence).to_contain_text("L4 (table header): | Metric | FY2025 | FY2026 |")
        expect(evidence).to_contain_text("L6 (cited): | Operating margin | 22.8% | 24.6% |")
        expect(evidence).to_contain_text("L8 (note): (1) Revenue restated")
        # Unmapped prose number is reachable and explicitly unresolved.
        page.get_by_role("button", name="1, uncited", exact=True).click()
        expect(evidence).to_contain_text("1: uncited")
        expect(page.get_by_label(SUPPORT, exact=True)).to_have_value("")


def test_stale_tab_offers_explicit_recovery_without_overwrite(tmp_path):
    b = margin_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = launch(pw)
        first, second = browser.new_page(), browser.new_page()
        for page in (first, second):
            page.goto(h.url)
            expect(page.locator("#status")).to_contain_text("revision 0")
            page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        first.get_by_label(SUPPORT, exact=True).select_option("supported")
        expect(first.locator("#status")).to_contain_text("revision 1")
        second.get_by_label(SUPPORT, exact=True).select_option("unsupported")
        conflict = second.get_by_role("alert")
        expect(conflict).to_contain_text("Nothing is overwritten until you choose")
        expect(conflict).to_contain_text("Differs from saved: " + SUPPORT)
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:margin"]["value"] == "supported"
        second.get_by_role("button", name="Keep this tab's answers as a new revision").click()
        expect(second.locator("#status")).to_contain_text("revision 2")
        expect(conflict).to_be_hidden()
        assert store.load_task(b.bundle_id)["answers"]["judgments"]["support:margin"]["value"] == "unsupported"
        first.get_by_label("Explanation: " + SUPPORT, exact=True).fill("first tab note")
        expect(first.get_by_role("alert")).to_be_visible()
        first.get_by_role("button", name="Use saved version").click()
        expect(first.get_by_label(SUPPORT, exact=True)).to_have_value("unsupported")
        expect(first.locator("#status")).to_contain_text("revision 2")
        assert len([e for e in (tmp_path / b.bundle_id / "events.jsonl").read_text().splitlines() if '"answer_saved"' in e]) == 2
        browser.close()


def test_keyboard_only_review_and_narrow_zoom(tmp_path):
    with open_review(margin_bundle(), FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = launch(pw)
        page = browser.new_page(viewport={"width": 640, "height": 900})
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "assessor"
        page.keyboard.type("Keyboard reviewer")
        number = page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="180 bps, derived", exact=True)
        for _ in range(10):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.getAttribute('aria-label')") == "180 bps, derived":
                break
        else:
            raise AssertionError("number button not reachable by Tab")
        assert number.evaluate("n => getComputedStyle(n).outlineStyle") == "solid"
        page.keyboard.press("Enter")
        page.get_by_role("button", name="Preview calculation evidence", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("region", name="Evidence")).to_contain_text("Formula: (current - prior) * 100")
        page.locator("body").click(position={"x": 1, "y": 1})
        page.keyboard.press("/")
        assert page.evaluate("document.activeElement.id") == "search"
        page.keyboard.type("restated")
        line = page.get_by_role("button", name="Synthetic filing L8: (1) Revenue restated for a disposed segment.")
        line.focus()
        page.keyboard.press("Enter")
        source_line = page.get_by_role("button", name="L8: (1) Revenue restated for a disposed segment.", exact=True)
        expect(source_line).to_have_attribute("aria-pressed", "true")
        select = page.get_by_label(SUPPORT, exact=True)
        select.focus()
        select.select_option("supported")  # native select via keyboard focus
        expect(page.locator("#status")).to_contain_text("revision 1")
        # 200% zoom equivalent: single column and no horizontal page scroll.
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # Status is conveyed by text and shape, not colour alone.
        assert page.get_by_role("button", name="1, uncited", exact=True).evaluate(
            "n => getComputedStyle(n, '::after').content"
        ) == '" ?"'
        browser.close()


def test_independent_browser_traffic_carries_no_blinded_marker(tmp_path):
    markers = ["arm-secret-7", "judge-verdict-clean", "synthetic-model-x"]
    hostile = TABLE + "<script>window.pwned=1</script><img src=x onerror=\"window.pwned=2\">\n"
    b = margin_bundle(text=hostile)
    seen = []
    with open_review(b, FileStore(tmp_path), launch=False, blind_markers=markers) as h, sync_playwright() as pw:
        browser = launch(pw)
        page = browser.new_page()

        def capture(response):
            try:
                seen.append(response.text())
            except Exception:
                pass

        page.on("response", capture)
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        assert "token" not in page.url
        page.get_by_label("Assessor", exact=True).fill("Synthetic reviewer")
        page.get_by_label(SUPPORT, exact=True).select_option("supported")
        page.get_by_label("I reviewed the full report.", exact=True).check()
        expect(page.locator("#status")).to_contain_text("revision 2")
        page.locator("#evidence-panel > summary").click()
        page.get_by_label("Search frozen sources (press /)").fill("script")
        page.get_by_role("button", name="Synthetic filing L11", exact=False).first.click()
        page.get_by_role("button", name="Submit review", exact=True).click()
        expect(page.locator("#continuation")).to_contain_text("Continuation succeeded")
        assert page.evaluate("window.pwned") is None
        seen.append(page.content())
        seen.append(json.dumps(page.evaluate("[location.href, history.length, document.title]")))
        browser.close()
    blob = "\n".join(seen).casefold()
    assert len(seen) > 8
    assert not [m for m in markers if m in blob]
    # Negative control: the same detector finds a marker that is present.
    assert "window.pwned" in blob


def test_citations_link_to_the_original_lines_and_unresolved_numbers_search(tmp_path):
    b = margin_bundle()
    sid = b.sources[0].source_id
    base = "https://github.com/o/r/blob/abc/doc.md?plain=1"
    links = {sid: {"url": base, "line_url": base + "#L{start}-L{end}", "label": "Open in source repo", "note": "same bytes as the frozen copy"}}
    with open_review(b, FileStore(tmp_path), launch=False, source_links=links) as h, sync_playwright() as pw:
        page = launch(pw).new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="Preview evidence for 24.6%", exact=True).click()
        evidence = page.get_by_role("region", name="Evidence")
        link = evidence.get_by_role("link", name="Open in source repo, L6").first
        assert link.get_attribute("href") == base + "#L6-L6"
        assert link.get_attribute("target") == "_blank"
        assert "noopener" in link.get_attribute("rel")
        expect(evidence).to_contain_text("same bytes as the frozen copy")
        page.get_by_role("button", name="1, uncited", exact=True).click()
        expect(page.locator("#search")).to_have_value("1")
        expect(evidence).to_contain_text("a match does not imply support")
    with open_review(margin_bundle(), FileStore(tmp_path / "nolinks"), launch=False) as h, sync_playwright() as pw:
        page = launch(pw).new_page()
        page.goto(h.url)
        page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="24.6%, cited", exact=True).click()
        expect(page.get_by_role("region", name="Evidence")).to_contain_text("No public original is recorded")


def bound_bundle():
    """Each number carries its own evidence, as a producer that binds numbers would send."""
    b = margin_bundle()
    claim = b.claims[0]
    spans = []
    for n in b.spans:
        if n.text == "24.6%":
            n = n.model_copy(update={"citations": [claim.citations[1]]})
        elif n.text == "180 bps":
            n = n.model_copy(update={"calculation": claim.calculation})
        spans.append(n)
    return b.model_validate({**b.model_dump(mode="json"), "spans": [s.model_dump(mode="json") for s in spans]})


def test_bound_numbers_open_their_line_and_calculations_link_every_input(tmp_path):
    b = bound_bundle()
    sid = b.sources[0].source_id
    base = "https://github.com/o/r/blob/abc/doc.md?plain=1"
    links = {sid: {"url": base, "line_url": base + "#L{start}-L{end}", "label": "Open in source repo"}}
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, source_links=links) as h, sync_playwright() as pw:
        page = launch(pw).new_page()
        # Offline test: answer the original's URL locally instead of reaching GitHub.
        page.context.route("https://github.com/**", lambda r: r.fulfill(body="original", content_type="text/plain"))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        report = page.get_by_role("region", name="Report")
        direct = report.get_by_role("link", name="24.6%, cited, opens the cited line of the original")
        assert direct.get_attribute("href") == base + "#L6-L6"
        assert direct.get_attribute("target") == "_blank"
        with page.context.expect_page() as opened:
            direct.click()
        assert opened.value.url.startswith("https://github.com/o/r/blob/abc/doc.md")
        report.get_by_role("button", name="Preview evidence for 24.6%", exact=True).click()
        evidence = page.get_by_role("region", name="Evidence")
        expect(evidence).to_contain_text("24.6%: cited")
        expect(evidence.get_by_role("button", name="Whole claim margin")).to_be_visible()
        page.get_by_role("region", name="Report", exact=True).get_by_role("button", name="180 bps, derived", exact=True).click()
        page.get_by_role("button", name="Preview calculation evidence", exact=True).click()
        expect(evidence.get_by_role("heading", name="180 bps", exact=True)).to_be_visible()
        expect(evidence).to_contain_text("Formula: (current - prior) * 100")
        for value in ["24.6", "22.8"]:
            expect(evidence.get_by_role("link", name=value, exact=True)).to_have_attribute("href", base + "#L6-L6")
        expect(evidence).to_contain_text("current: 24.6")
        expect(evidence).to_contain_text("prior: 22.8")
