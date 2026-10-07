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
        expect(page.locator('#evidence .number.prepared')).to_have_count(1)
        expect(page.locator('#evidence input[type=checkbox]')).to_have_count(0)
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

def test_pane_dividers_drag_and_remember_sizes(tmp_path):
    b=reference_quantity_bundle()
    with open_review(b,FileStore(tmp_path),launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1100})
        page.goto(h.url);page.wait_for_selector('#items select')
        top=page.locator('[aria-label="Evidence"]');left=page.locator('[aria-label="Report"]')
        horizontal=page.get_by_role('separator',name='Resize evidence height')
        vertical=page.get_by_role('separator',name='Resize left pane width')
        expect(horizontal).to_be_visible();expect(vertical).to_be_visible()
        before_height=top.bounding_box()['height']; before_width=left.bounding_box()['width']
        point=horizontal.bounding_box();page.mouse.move(point['x']+point['width']/2,point['y']+point['height']/2)
        page.mouse.down();page.mouse.move(point['x']+point['width']/2,point['y']+point['height']/2+60);page.mouse.up()
        assert top.bounding_box()['height'] > before_height+40
        point=vertical.bounding_box();page.mouse.move(point['x']+point['width']/2,point['y']+point['height']/2)
        page.mouse.down();page.mouse.move(point['x']+point['width']/2+80,point['y']+point['height']/2);page.mouse.up()
        assert left.bounding_box()['width'] > before_width+60
        width=left.bounding_box()['width'];height=top.bounding_box()['height']
        page.reload();page.wait_for_selector('#items select')
        assert abs(left.bounding_box()['width']-width)<2
        assert abs(top.bounding_box()['height']-height)<2
        vertical.focus();page.keyboard.press('ArrowLeft')
        assert left.bounding_box()['width'] < width
        expect(page.locator('#status')).to_contain_text('revision 0')
        browser.close()

def test_evidence_list_return_keeps_reference_with_multiple_claim_links(tmp_path):
    b=reference_quantity_bundle()
    b.claims.append(b.claims[0].model_copy(update={'claim_id':'auxiliary-context'}))
    span=next(s for s in b.spans if s.prepared_evidence)
    span.claim_ids.append('auxiliary-context')
    b=validate_bundle(b.model_dump(mode='json'))
    with open_review(b,FileStore(tmp_path),launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page()
        page.goto(h.url);page.wait_for_selector('#evidence .number.prepared')
        page.locator('#evidence .number.prepared').click()
        page.get_by_role('button',name='All evidence links',exact=True).click()
        expect(page.locator('#evidence .number.prepared')).to_have_count(1)
        expect(page.locator('#evidence').get_by_role('button',name='Supporting factual evidence',exact=True)).to_be_visible()
        browser.close()
