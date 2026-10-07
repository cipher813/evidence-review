from playwright.sync_api import sync_playwright, expect
from evidence_review import FileStore, open_review
from evidence_review.contracts import validate_bundle, PreparedEvidence, ReferenceItem
from test_quantity_checks import quantity_bundle

def reference_quantity_bundle():
    b=quantity_bundle()
    b.task_kind='reference'
    b.references=[ReferenceItem(reference_id=c.claim_id,text=c.text,citations=c.citations,calculation=c.calculation) for c in b.claims]
    span=next(s for s in b.spans if any(f.numeric_span_id==s.span_id for f in b.form))
    span.prepared_evidence=[PreparedEvidence(calculation=b.claims[0].calculation, citations=b.claims[0].citations, reason='Exact prepared source evidence')]
    span.state='uncited'
    return validate_bundle(b.model_dump(mode='json'))

def test_reference_layout_has_clean_left_inline_right_and_click_evidence(tmp_path):
    b=reference_quantity_bundle()
    with open_review(b,FileStore(tmp_path),launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto(h.url);expect(page.locator('#items select').first).to_be_visible()
        expect(page.locator('[aria-label="Report"] input[type=checkbox]')).to_have_count(0)
        expect(page.locator('#report .number')).to_have_count(0)
        expect(page.locator('#evidence')).to_contain_text('Click a quantity')
        expect(page.locator('#items')).not_to_contain_text('Select the claims')
        expect(page.locator('#items')).not_to_contain_text('Coverage:')
        checks=page.locator('#items input[data-quantity-field]');expect(checks).to_have_count(1)
        action=page.locator('#items .number.prepared');expect(action).to_have_count(1)
        label=checks.first.locator('..')
        assert abs(checks.first.bounding_box()['y']-action.first.bounding_box()['y']) < 8
        assert action.first.locator('..').evaluate('(n)=>getComputedStyle(n).display')=='inline-flex'
        action.first.click()
        expect(page.locator('#evidence')).to_contain_text('Exact prepared source evidence')
        expect(page.locator('#evidence')).to_contain_text('Prepared calculation')
        expect(page.locator('#evidence')).not_to_contain_text('Candidate-supplied')
        browser.close()
