"""Partial navigation is attributed, source-bound and compatible with old stores."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from evidence_review.contracts import ReviewBundle
from evidence_review.evidence import evidence_views
from test_browser_review import margin_bundle


def partial_bundle():
    raw = margin_bundle().model_dump(mode='json')
    span = next(s for s in raw['spans'] if s['text'] == '180 bps')
    span['prepared_inputs'] = [deepcopy(span['calculation']['operands'][0])]
    return raw


def test_partial_navigation_is_validated_served_and_hashed():
    raw = partial_bundle()
    b = ReviewBundle.model_validate(raw)
    n = next(s for s in b.spans if s.text == '180 bps')
    assert n.prepared_inputs[0].value == '24.6'
    assert 'filing:6:6' in evidence_views(b)
    assert b.bundle_hash != margin_bundle().bundle_hash
    raw['spans'] = [dict(s, prepared_inputs=[]) for s in raw['spans']]
    assert ReviewBundle.model_validate(raw).bundle_hash == margin_bundle().bundle_hash


@pytest.mark.parametrize('change', ['name', 'value', 'period', 'unit', 'citation'])
def test_partial_navigation_cannot_replace_an_incompatible_candidate_input(change):
    raw = partial_bundle()
    n = next(s for s in raw['spans'] if s['text'] == '180 bps')
    o = n['prepared_inputs'][0]
    if change == 'citation': o['citation']['excerpt'] = 'not in source'
    else: o[change] = {'name':'unknown', 'value':'99', 'period':'FY1900', 'unit':'EUR'}[change]
    with pytest.raises(ValidationError): ReviewBundle.model_validate(raw)


def test_linked_subtotal_is_concise_before_optional_diagnostics(tmp_path):
    from evidence_review import FileStore, open_review
    from evidence_review.contracts import Calculation, Operand, Citation
    from playwright.sync_api import sync_playwright, expect
    raw = partial_bundle()
    n = next(s for s in raw['spans'] if s['text'] == '180 bps')
    original = n['calculation']['operands'][0]
    original['citation'].update(status='excerpt_mismatch', excerpt='24 ... 0.6', reason='excerpt does not match')
    n['calculation']['operands'][0] = original
    leaves = [Operand(name='part1', value='24', unit='pct', period='FY2026', entity='Segment A',
                      citation=Citation(source_id='filing', start_line=6, end_line=6, excerpt='24.6%', status='located')),
              Operand(name='part2', value='0.6', unit='pct', period='FY2026', entity='Segment B',
                      citation=Citation(source_id='filing', start_line=6, end_line=6, excerpt='24.6%', status='located'))]
    derived = Operand(name='current', value='24.6', unit='pct', period=original['period'], kind='derived',
                      calculation=Calculation(formula='part1+part2', operands=leaves, result='24.6', unit='pct'))
    n['prepared_inputs'] = [derived.model_dump(mode='json')]
    b = ReviewBundle.model_validate(raw)
    links = {'filing': {'line_url': 'https://github.com/example/synthetic/blob/' + 'a'*40 + '/source.md#L{start}-L{end}'}}
    links['filing']['url'] = links['filing']['line_url'].split('#')[0]
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, source_links=links) as h, sync_playwright() as pw:
        browser = pw.chromium.launch(); page = browser.new_page(); page.goto(h.url)
        before = store.load_task(b.bundle_id)['answers']
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='180 bps, derived', exact=True).click()
        card = page.get_by_role('region', name='Calculation details')
        for value in ['24', '0.6']:
            expect(card.get_by_role('link', name=value, exact=True)).to_have_attribute('href', links['filing']['line_url'].format(start=6, end=6))
        expect(card).to_contain_text('Located inputs (independent)')
        expect(card).to_contain_text('Candidate citation')
        expect(card.get_by_text('Formula literal (source association not established): 100', exact=True)).not_to_be_visible()
        card.get_by_role('button', name='Preview calculation evidence', exact=True).click()
        panel = page.get_by_role('region', name='Evidence', exact=True)
        expect(panel.get_by_role('link', name='24', exact=True)).to_be_visible()
        expect(panel.get_by_role('link', name='0.6', exact=True)).to_be_visible()
        assert store.load_task(b.bundle_id)['answers'] == before
        browser.close()


def test_equal_decimal_representations_keep_source_navigation():
    raw = partial_bundle()
    n = next(s for s in raw['spans'] if s['text'] == '180 bps')
    n['calculation']['operands'][0]['value'] = '24.60'
    assert ReviewBundle.model_validate(raw).spans[0].prepared_inputs[0].value == '24.6'


@pytest.mark.parametrize('derived', [False, True])
def test_prepared_inputs_require_a_source_leaf_not_only_constants(derived):
    raw = partial_bundle()
    n = next(s for s in raw['spans'] if s['text'] == '180 bps')
    o = n['prepared_inputs'][0]
    o.update(kind='constant', citation=None)
    if derived:
        leaf = deepcopy(o); leaf['name'] = 'constant'
        o.update(kind='derived', calculation={'formula': 'constant', 'operands': [leaf], 'result': '24.6', 'unit': 'pct'})
    with pytest.raises(ValidationError): ReviewBundle.model_validate(raw)


@pytest.mark.parametrize("preview_kind", ["calculation", "input"])
def test_calculation_preview_resets_prior_claim_and_selected_passage(tmp_path, preview_kind):
    from evidence_review import FileStore, open_review
    from playwright.sync_api import sync_playwright, expect
    raw = margin_bundle().model_dump(mode='json')
    other = deepcopy(raw['claims'][0]); other['claim_id'] = 'other'
    raw['claims'].append(other)
    raw['fields'][0]['claim_ids'] = ['other']
    for n in raw['spans']:
        if n['field_path'] == raw['fields'][0]['path']: n['claim_ids'] = ['other']
    b = ReviewBundle.model_validate(raw); store = FileStore(tmp_path)
    with open_review(b, store, launch=False) as h, sync_playwright() as pw:
        browser = pw.chromium.launch(); page = browser.new_page(); page.goto(h.url)
        page.get_by_label('Assessor', exact=True).fill('Synthetic reviewer')
        page.get_by_role('button', name='Sources and rubric', exact=True).click()
        page.get_by_role('button', name='Full frozen preview: Synthetic filing', exact=True).click()
        row = page.get_by_role('button', name='L6:', exact=False)
        row.click(); row.click()
        page.get_by_role('region', name='Report', exact=True).get_by_role('button', name='180 bps, derived', exact=True).click()
        page.get_by_role('button', name='Preview calculation evidence' if preview_kind == 'calculation' else 'Preview input current', exact=True).click()
        page.get_by_role('button', name='Add defect', exact=True).click()
        expect(page.locator('#status')).to_contain_text('revision 1')
        defect = store.load_task(b.bundle_id)['answers']['defects'][0]
        assert defect['claim_ids'] == ['other']
        assert defect['selections'] == []
        browser.close()
