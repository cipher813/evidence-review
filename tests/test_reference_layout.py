import hashlib
import pytest
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

@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r", "\u2028"])
def test_checked_reference_submits_without_reselecting_source_lines(tmp_path, newline):
    b=reference_quantity_bundle()
    b.form[0].evidence_required=True
    for source in b.sources:
        source.text=newline.join(source.text.splitlines())
        source.sha256=hashlib.sha256(source.text.encode()).hexdigest()
    b=validate_bundle(b.model_dump(mode='json'))
    store=FileStore(tmp_path)
    with open_review(b,store,launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page()
        page.goto(h.url);page.wait_for_selector('#items select')
        page.locator('#items select').first.select_option('supported')
        page.locator('#items input[data-quantity-field]').first.check()
        page.locator('#forms input[type=checkbox]').check()
        page.locator('#submit').click()
        expect(page.locator('#status')).not_to_contain_text('Not submitted')
        expect(page.locator('#status')).to_contain_text('Submitted')
        state=store.load_task(b.bundle_id)
        verdict=state['answers']['judgments'][b.form[0].field_id]
        assert verdict['value']=='supported'
        assert verdict['selections']
        source=next(s for s in b.sources if s.source_id==verdict['selections'][0]['source_id'])
        assert verdict['selections'][0]['source_hash']==source.sha256
        assert state['answers']['judgments']['quantity']['value'] is True
        page.reload();page.wait_for_selector('#items select')
        expect(page.locator('#items input[data-quantity-field]').first).to_be_checked()
        expect(page.locator('#items')).to_contain_text('Automatic evidence receipt')
        expect(page.locator('#items').get_by_role('button',name='Remove passage',exact=True)).to_have_count(0)
        browser.close()

def test_reference_receipts_do_not_invent_checks_or_duplicate_on_save(tmp_path):
    b=reference_quantity_bundle()
    b.form[0].evidence_required=True
    b=validate_bundle(b.model_dump(mode='json'))
    store=FileStore(tmp_path)
    with open_review(b,store,launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page()
        page.goto(h.url);page.wait_for_selector('#items select')
        with page.expect_response(lambda r:r.url.endswith('/api/save')):
            page.locator('#items select').first.select_option('supported')
        assert store.load_task(b.bundle_id)['answers']['judgments'][b.form[0].field_id]['selections']==[]
        with page.expect_response(lambda r:r.url.endswith('/api/save')):
            page.locator('#items input[data-quantity-field]').first.check()
        before=store.load_task(b.bundle_id)['answers']['judgments'][b.form[0].field_id]['selections']
        assert before
        with page.expect_response(lambda r:r.url.endswith('/api/save')):
            page.locator('#items textarea').fill('Reviewed')
        assert store.load_task(b.bundle_id)['answers']['judgments'][b.form[0].field_id]['selections']==before
        with page.expect_response(lambda r:r.url.endswith('/api/save')):
            page.locator('#items input[data-quantity-field]').first.uncheck()
        page.locator('#forms input[type=checkbox]').check()
        page.locator('#submit').click()
        expect(page.locator('#status')).to_contain_text('unchecked quantity')
        page.reload();page.wait_for_selector('#items select')
        assert page.locator('#items').get_by_role('button',name='Remove passage',exact=True).count()>0
        with page.expect_response(lambda r:r.url.endswith('/api/save')):
            page.locator('#items input[data-quantity-field]').first.check()
        expect(page.locator('#items').get_by_role('button',name='Remove passage',exact=True)).to_have_count(0)
        expect(page.locator('#items')).to_contain_text('Automatic evidence receipt')
        assert store.load_task(b.bundle_id)['answers']['judgments'][b.form[0].field_id]['selections']==before
        browser.close()


@pytest.mark.parametrize("mode", ["missing_evidence", "candidate"])
def test_receipts_preserve_missing_evidence_and_candidate_gates(tmp_path, mode):
    b=reference_quantity_bundle()
    b.form[0].evidence_required=True
    if mode=="candidate":
        b.task_kind="finding"
    else:
        span=next(s for s in b.spans if s.prepared_evidence)
        span.prepared_evidence=[]
        span.citations=[]
        span.calculation=None
    b=validate_bundle(b.model_dump(mode='json'))
    store=FileStore(tmp_path)
    with open_review(b,store,launch=False,assessor='Scratch') as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page()
        page.goto(h.url);page.wait_for_selector('#items select')
        page.locator('#items select').first.select_option('supported')
        page.locator('#items input[data-quantity-field]').first.check()
        page.locator('#forms input[type=checkbox]').check()
        page.locator('#submit').click()
        expect(page.locator('#status')).to_contain_text('evidence required')
        assert store.load_task(b.bundle_id)['answers']['judgments'][b.form[0].field_id]['selections']==[]
        browser.close()
