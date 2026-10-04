"""Real Chromium proves numeric/source navigation, independent inputs and recovery."""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from evidence_review import open_review
from evidence_review.example import example_bundle
from evidence_review.store import FileStore


def test_browser_review_reload_amend_and_safe_text(tmp_path):
    bundle=example_bundle();store=FileStore(tmp_path/'state')
    with open_review(bundle,store,launch=False) as handle,sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page();page.goto(handle.url)
        expect(page.get_by_role('status')).to_contain_text('Saved locally')
        page.get_by_label('Assessor',exact=True).fill('Synthetic reviewer')
        page.get_by_label('Assessor',exact=True).press('Tab')
        page.get_by_role('button',name='180 bps',exact=True).click()
        expect(page.get_by_role('region',name='Evidence')).to_contain_text('current-prior')
        expect(page.get_by_role('region',name='Evidence')).to_contain_text('22.8')
        page.get_by_role('button',name='Open full frozen source').first.click()
        expect(page.get_by_role('region',name='Evidence')).to_contain_text('Footnote')
        page.get_by_role('button',name='L4: | 2025 | 22.8% |',exact=True).click()
        page.get_by_role('button',name='L4: | 2025 | 22.8% |',exact=True).click()
        page.get_by_label('Does the evidence support the margin claim?',exact=True).select_option('supported')
        expect(page.get_by_role('status')).to_contain_text('revision 1')
        page.get_by_role('button',name='Attach selected source passage',exact=True).first.click()
        expect(page.get_by_role('status')).to_contain_text('revision 2')
        page.reload()
        expect(page.get_by_label('Does the evidence support the margin claim?',exact=True)).to_have_value('supported')
        expect(page.get_by_role('region',name='Judgments')).to_contain_text('L4–L4')
        page.get_by_label('I reviewed the full report and recorded all identified material defects.',exact=True).check()
        expect(page.get_by_role('status')).to_contain_text('revision 3')
        page.get_by_role('button',name='Submit review',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('continuation succeeded')
        page.get_by_label('Amendment reason',exact=True).fill('Synthetic second inspection')
        page.get_by_role('button',name='Submit review',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('revision 5')
        assert len(store.load_task(bundle.bundle_id)['submissions'])==2
        page.get_by_role('button',name='99%',exact=True).click()
        expect(page.get_by_role('region',name='Evidence')).to_contain_text('uncited')
        # Report/source text is never interpreted as executable markup.
        assert page.locator('script').count()==1
        page.get_by_role('button',name='Next task',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Queue complete')
        browser.close()
