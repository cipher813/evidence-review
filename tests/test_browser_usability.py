"""Synthetic source actions, one active judgment and honest work counts."""
from playwright.sync_api import expect, sync_playwright
from evidence_review import FileStore, open_review
from evidence_review.contracts import ReviewBundle
from test_browser_review import bound_bundle, SUPPORT

BASE = 'https://github.com/example/synthetic/blob/' + 'a' * 40 + '/financial%20data.md?plain=1'
LINKS = {'filing': {'url': BASE, 'line_url': BASE + '#L{start}-L{end}', 'label': 'Frozen original'}}


def test_primary_link_keyboard_and_preview_do_not_change_judgments(tmp_path):
    b = bound_bundle()
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.context.route('https://github.com/**', lambda r: r.fulfill(body='synthetic original'))
        page.goto(h.url)
        expect(page.locator('#status')).to_contain_text('Saved locally')
        original_current = page.get_by_label('Current item').get_by_role('heading').first.inner_text()
        number = page.get_by_role('region', name='Report', exact=True).get_by_role('link', name='24.6%, cited, opens the cited line of the original', exact=True)
        number.focus()
        with page.context.expect_page() as opened:
            page.keyboard.press('Enter')
        assert opened.value.url == BASE + '#L6-L6'
        expect(page.get_by_label(SUPPORT, exact=True)).to_have_value('')
        assert page.get_by_label('Current item').get_by_role('heading').first.inner_text() == original_current
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='Preview evidence for 24.6%', exact=True).click()
        expect(page.get_by_role('region', name='Evidence', exact=True)).to_be_visible()
        expect(page.get_by_role('region', name='Evidence', exact=True).get_by_role('table')).to_have_count(1)
        expect(page.locator('#status')).to_contain_text('0/2 fields answered')
        browser.close()
    assert store.load_task(b.bundle_id)['answers']['judgments'] == {}


def test_derived_input_values_are_direct_links_in_inline_card_and_preview(tmp_path):
    with open_review(bound_bundle(), FileStore(tmp_path), launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='180 bps, derived', exact=True).click()
        card = page.get_by_role('region', name='Calculation details', exact=True)
        expect(card).to_contain_text('Reported result: 180 bps')
        expect(card.get_by_text('Original candidate calculation', exact=True)).to_be_visible()
        for value in ['24.6', '22.8']:
            link = card.get_by_role('link', name=value, exact=True)
            expect(link).to_have_attribute('href', BASE + '#L6-L6')
            expect(link).to_have_attribute('rel', 'noopener noreferrer')
        card.get_by_role('button', name='Preview calculation evidence', exact=True).click()
        preview = page.get_by_role('region', name='Evidence', exact=True)
        expect(preview.get_by_role('link', name='24.6', exact=True)).to_have_count(1)
        expect(preview.get_by_role('link', name='22.8', exact=True)).to_have_count(1)
        expect(preview).to_contain_text('Formula literal (source association not established): 100')
        expect(page.get_by_label(SUPPORT, exact=True)).to_have_value('')
        browser.close()


def test_broad_document_link_is_not_an_exact_number_destination(tmp_path):
    with open_review(bound_bundle(), FileStore(tmp_path), launch=False, source_links={'filing': {'url': BASE}}) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        report = page.get_by_role('region', name='Report', exact=True)
        expect(report.get_by_role('link', name='24.6%, cited', exact=False)).to_have_count(0)
        report.get_by_role('button', name='24.6%, cited', exact=True).click()
        expect(page.get_by_role('region', name='Evidence', exact=True)).to_contain_text('No exact source link')
        browser.close()


def test_counts_distinguish_assigned_answers_judgments_optional_and_completion(tmp_path):
    raw = bound_bundle().model_dump(mode='json')
    raw['workload'] = {'answer_index': 1, 'assigned_answers': 3, 'task_counts': {'independent': 3, 'reference': 2}, 'assignment_reason': 'Assigned seeded sample', 'expansion_conditions': ['Failure may require additional tasks; none assigned yet']}
    raw['form'].append({'field_id': 'optional', 'label': 'Optional note', 'kind': 'text', 'required': False})
    b = ReviewBundle.model_validate(raw)
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        expect(page.locator('#workload')).to_contain_text('Answer 1 of 3 assigned')
        expect(page.locator('#workload')).to_contain_text('reference: 2')
        expect(page.locator('#workload')).to_contain_text('Assigned seeded sample')
        expect(page.get_by_label('Review progress')).to_contain_text('Judgment 1 of 1 in this answer')
        expect(page.get_by_label('Review progress')).to_contain_text('1 optional field')
        expect(page.get_by_label('Review progress')).to_contain_text('1 completion control')
        expect(page.get_by_label('Current item')).to_contain_text('Operating margin rose')
        browser.close()


def test_required_explanation_and_passage_remain_pending_until_complete(tmp_path):
    raw = bound_bundle().model_dump(mode='json')
    raw['form'][0]['evidence_required'] = True
    b = ReviewBundle.model_validate(raw)
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_label('Assessor', exact=True).fill('Synthetic reviewer')
        page.get_by_label(SUPPORT, exact=True).select_option('unsupported')
        expect(page.get_by_label('Review progress')).to_contain_text('0 of 2 required fields complete')
        expect(page.get_by_label('Review progress')).to_contain_text('Pending: explanation and source passage')
        browser.close()


def test_ambiguous_mapping_never_becomes_primary_link_and_has_a_source_chooser(tmp_path):
    raw = bound_bundle().model_dump(mode='json')
    n = next(n for n in raw['spans'] if n['text'] == '24.6%')
    n['state'], n['reason'] = 'ambiguous', 'Repeated value in multiple periods'
    b = ReviewBundle.model_validate(raw)
    with open_review(b, FileStore(tmp_path), launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        report = page.get_by_role('region', name='Report', exact=True)
        report.get_by_role('button', name='24.6%, ambiguous', exact=True).click()
        preview = page.get_by_role('region', name='Evidence', exact=True)
        expect(preview).to_contain_text('Multiple possible sources—no exact match established')
        expect(preview.get_by_role('group', name='Source choices')).to_be_visible()
        browser.close()


def test_second_required_field_on_same_subject_remains_reachable(tmp_path):
    raw = bound_bundle().model_dump(mode='json')
    raw['form'].insert(1, {'field_id': 'interpretation', 'label': 'Interpretation support', 'options': ['supported', 'unsupported'], 'subject_id': 'margin'})
    b = ReviewBundle.model_validate(raw)
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(h.url)
        page.get_by_label('Assessor', exact=True).fill('Synthetic reviewer')
        page.get_by_label(SUPPORT, exact=True).select_option('supported')
        page.get_by_role('button', name='Next unanswered item', exact=True).click()
        expect(page.get_by_label('Interpretation support', exact=True)).to_be_visible()
        expect(page.get_by_label('Current item')).to_contain_text('Interpretation support')
        browser.close()


def test_unbound_ambiguous_number_does_not_borrow_whole_claim_calculation(tmp_path):
    raw = bound_bundle().model_dump(mode='json')
    n = next(n for n in raw['spans'] if n['text'] == '24.6%')
    n.update(state='ambiguous', reason='Same value appears in different years', citations=[], context={'metric': 'Margin', 'entity': 'Segment A', 'period': 'FY2024', 'unit': 'pct', 'status': 'ambiguous'})
    b = ReviewBundle.model_validate(raw)
    with open_review(b, FileStore(tmp_path), launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch(); page = browser.new_page(); page.goto(h.url)
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='24.6%, ambiguous', exact=True).click()
        preview = page.get_by_role('region', name='Evidence', exact=True)
        expect(preview).to_contain_text('Multiple possible sources—no exact match established')
        expect(preview).to_contain_text('Same value appears in different years')
        expect(preview).to_contain_text('Segment A')
        expect(preview).not_to_contain_text('Recomputed (Decimal)')
        browser.close()


def test_prepared_subtotal_is_visible_inline_and_preview_with_original_defect(tmp_path):
    from evidence_review.contracts import Calculation, Operand, PreparedEvidence, Citation
    b = bound_bundle()
    span = next(n for n in b.spans if n.text == '180 bps')
    original = span.calculation.model_copy(deep=True)
    original.formula = '(13 + 17) / 1000'
    original.operands[0].citation = Citation(source_id='filing', start_line=6, end_line=6, excerpt='short ... excerpt', status='excerpt_mismatch', reason='not verbatim')
    span.calculation = original
    leaf = b.claims[0].calculation.operands[0].model_copy(deep=True)
    leaf.name, leaf.value = 'component', '24.6'
    subtotal = Calculation(formula='component', operands=[leaf], result='24.6', unit='pct')
    calc = Calculation(formula='subtotal * scale', operands=[Operand(name='subtotal', value='24.6', unit='pct', kind='derived', calculation=subtotal), Operand(name='scale', value='100', kind='constant')], result='2460', unit='bps', conversions=['Percent to basis points: multiply by 100'])
    span.prepared_evidence = [PreparedEvidence(origin='independently_located', calculation=calc, reason='Explicit source inputs; not a support verdict')]
    with open_review(b, FileStore(tmp_path), launch=False, source_links=LINKS) as h, sync_playwright() as pw:
        browser = pw.chromium.launch(); page = browser.new_page(); page.goto(h.url)
        current = page.get_by_label('Current item').get_by_role('heading').first.inner_text()
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='180 bps, derived', exact=True).click()
        card = page.get_by_role('region', name='Calculation details', exact=True)
        expect(card).to_contain_text('Prepared calculation')
        expect(card).to_contain_text('Original candidate calculation')
        expect(card).to_contain_text('Candidate citation')
        expect(card.get_by_role('link', name='24.6', exact=True)).to_have_attribute('href', BASE + '#L6-L6')
        expect(card).to_contain_text('Candidate citation')
        expect(card).not_to_contain_text('Mathematical constant: 13')
        expect(card).to_contain_text('Mathematical constant: 100')
        card.get_by_role('button', name='Preview calculation evidence', exact=True).click()
        preview = page.get_by_role('region', name='Evidence', exact=True)
        expect(preview).to_contain_text('Prepared calculation')
        expect(preview).to_contain_text('Original candidate calculation')
        assert page.get_by_label(SUPPORT, exact=True).input_value() == ''
        assert current == page.get_by_label('Current item').get_by_role('heading').first.inner_text()
        browser.close()


def test_resource_guide_lists_every_frozen_source_and_reference_status(tmp_path):
    b = bound_bundle()
    b.sources[0].metadata = {'type': 'filing', 'period': 'FY2026'}
    with open_review(b, FileStore(tmp_path), launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch(); page = browser.new_page(); page.goto(h.url)
        page.get_by_role('button', name='Sources and rubric', exact=True).click()
        guide = page.get_by_role('region', name='Resource guide', exact=True)
        expect(guide).to_be_visible()
        expect(guide).to_contain_text('FY2026')
        expect(guide).to_contain_text('filing')
        expect(guide).to_contain_text('Sources remain the authority')
        expect(guide).to_contain_text('Reference verification status')
        page.get_by_role('button', name='Close sources and rubric', exact=True).click()
        for source in b.sources:
            page.get_by_role('button', name='Sources and rubric', exact=True).click()
            guide.get_by_role('button', name='Full frozen preview: ' + source.title, exact=True).click()
            expect(page.get_by_role('region', name='Evidence', exact=True)).to_contain_text(source.text.splitlines()[0])
        expect(page.get_by_label(SUPPORT, exact=True)).to_have_value('')
        browser.close()


def test_unparsed_multirow_table_preview_retains_all_column_headers(tmp_path):
    from evidence_review.contracts import Citation, digest
    b=bound_bundle()
    rows=['Issuer', '| | Quarter | Quarter | Year | Year |', '| --- | --- | --- | --- | --- |', '| | 2025 | 2024 | 2025 | 2024 |']
    rows += [f'| Row {i} | 1 | 2 | 3 | 4 |' for i in range(12)]
    rows += ['| Margin | 24.6% | 22.8% | 24.6% | 22.8% |']
    b.sources[0].text='\n'.join(rows);b.sources[0].sha256=digest(b.sources[0].text)
    c=Citation(source_id='filing',start_line=17,end_line=17,excerpt='24.6%',status='located')
    b.claims[0].citations=[c]
    b.claims[0].calculation=None
    for s in b.spans:
        s.calculation=None
        s.citations=[c] if s.text=='24.6%' else []
    with open_review(b,FileStore(tmp_path),launch=False) as h,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page();page.goto(h.url)
        page.get_by_role('region',name='Report',exact=True).get_by_role('button',name='24.6%, cited',exact=True).click()
        expect(page.get_by_role('region',name='Evidence',exact=True)).to_contain_text('L4: | | 2025 | 2024 | 2025 | 2024 |')
        browser.close()
