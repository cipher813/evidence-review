"""Audit regressions (evidence-review-I27, I29) in Chromium; every value is synthetic.

I27: a late rendered-source response or failure never paints over a newer click or another task.
I29: atomic mode shows one check control per assigned atom, and a caller-defined task noun.
"""
import json
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, Hooks, open_review, validate_bundle
from evidence_review.atomic_evidence import build_atom_manifest
from evidence_review.contracts import Claim, Source, digest
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset
from rendered_fixtures import html_bundle, html_sidecars, read
from synthetic import build, located

ATOMIC = "atomic-source-check/v1"
FIRST, SECOND = "First filing", "Second filing"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def race_bundle():
    """The auditor's A03 input: two numbers, each cited in its own source."""
    sources = [Source(source_id=sid, title=title, text=text, sha256=digest(text))
               for sid, title, text in (("first", FIRST, "A was 24.6%."), ("second", SECOND, "B was 37.8%."))]
    b = build([("summary", "A was 24.6%; B was 37.8%.", ["a", "b"])],
              [Claim(claim_id="a", text="A was 24.6%", citations=[located(1, "24.6%", "first")]),
               Claim(claim_id="b", text="B was 37.8%", citations=[located(1, "37.8%", "second")])],
              sources=sources, bundle_id="race")
    d = b.model_dump(mode="json")
    for s in d["spans"]:
        sid = "first" if s["text"] == "24.6%" else "second"
        s.update(state="cited", claim_ids=["a" if sid == "first" else "b"],
                 citations=[located(1, s["text"], sid).model_dump(mode="json")])
    return validate_bundle(d)


def opened(browser, h):
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    page.locator("#assessor").fill("Ada")
    return ctx, page, errors


def delay(page, suffix, ms, fail=False):
    """Hold one source-render response after it arrived; abort cannot cancel it, so only the fence can hold."""
    page.evaluate("""([suffix, ms, fail]) => {
      const f = window.fetch;
      window.fetch = async (...args) => {
        const r = await f(...args);
        if (String(args[0]).endsWith('/api/source-render/' + suffix)) {
          await new Promise((resolve) => setTimeout(resolve, ms));
          if (fail) throw new Error('synthetic late failure');
        }
        return r;
      };
    }""", [suffix, ms, fail])


def assert_latest(page, number, title, link):
    expect(page.locator("#viewer .target-hit")).to_contain_text(number)
    page.wait_for_timeout(1000)  # Past the held response.
    expect(page.locator("#viewer .target-hit")).to_have_count(1)
    expect(page.locator("#viewer .target-hit")).to_contain_text(number)
    expect(page.locator("#viewer .viewer-head h3")).to_have_text(title)
    expect(page.locator("#viewer")).not_to_contain_text("Rendered source unavailable")
    page.locator("#viewer").get_by_role("button", name="Back to row").click()
    expect(link).to_be_focused()


@pytest.mark.parametrize("held,order", [("first", (0, 1)), ("second", (1, 0))])
def test_late_response_never_paints_over_the_latest_click_in_either_order(tmp_path, browser, held, order):
    with open_review(race_bundle(), FileStore(tmp_path), launch=False, presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        delay(page, held, 800)
        links = page.locator(".atom-link")
        links.nth(order[0]).click()
        links.nth(order[1]).click()
        latest = ("24.6%", FIRST) if order[1] == 0 else ("37.8%", SECOND)
        assert_latest(page, *latest, links.nth(order[1]))
        assert not errors
        ctx.close()


def test_fast_response_then_slow_click_shows_the_slow_latest_target(tmp_path, browser):
    with open_review(race_bundle(), FileStore(tmp_path), launch=False, presentation_mode=ATOMIC) as h:
        ctx, page, _ = opened(browser, h)
        delay(page, "second", 600)
        links = page.locator(".atom-link")
        links.nth(0).click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("24.6%")
        links.nth(1).click()  # Newer click, slower response: it must win once it arrives.
        assert_latest(page, "37.8%", SECOND, links.nth(1))
        ctx.close()


def test_stale_failure_never_replaces_the_latest_target(tmp_path, browser):
    with open_review(race_bundle(), FileStore(tmp_path), launch=False, presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        delay(page, "first", 800, fail=True)
        links = page.locator(".atom-link")
        links.nth(0).click()
        links.nth(1).click()
        assert_latest(page, "37.8%", SECOND, links.nth(1))
        # Once it is the latest click, the same failure is shown: the fence hides only stale results.
        links.nth(0).click()
        expect(page.locator("#viewer")).to_contain_text("Rendered source unavailable: synthetic late failure")
        assert not errors
        ctx.close()


def post(h, path, body):
    req = Request(h.origin + path, data=json.dumps(body).encode(), method="POST",
                  headers={"Authorization": "Bearer " + h.token, "Content-Type": "application/json",
                           "Origin": h.origin})
    with urlopen(req) as r:
        return json.loads(r.read())


def test_late_response_from_a_previous_task_never_paints_into_the_next_task(tmp_path, browser):
    first, second = html_bundle(), html_bundle(task_kind="finding")
    queue = [second]
    hooks = Hooks(on_submission=lambda s: {"status": "succeeded", "identifier": "r"},
                  next_bundle=lambda: queue.pop() if queue else None)
    with open_review(first, FileStore(tmp_path), hooks=hooks, launch=False,
                     rendered_sources=lambda b: [html_sidecars(b)[1]],
                     atom_evidence=lambda b: html_sidecars(b)[0], presentation_mode=ATOMIC) as h:
        ctx, page, errors = opened(browser, h)
        answers = {"judgments": {"verdict:margin": {"value": "cannot_verify", "note": "Synthetic"},
                                 "report_complete": {"value": True}}, "defects": []}
        post(h, "/api/submit", {"bundle_id": first.bundle_id, "bundle_hash": first.bundle_hash, "revision": 0,
                                "key": "k", "answers": answers, "assessor": "Ada"})
        page.reload()
        expect(page.locator("#next")).to_be_enabled()
        delay(page, "filing", 1000)
        page.locator("[data-atom-row]").filter(has_text="“24.6%”").locator(".atom-link").click()
        with page.expect_response(lambda r: r.url.endswith("/api/next")) as nxt:
            page.locator("#next").click()
        assert nxt.value.ok, nxt.value.text()
        expect(page.locator("#task")).to_have_text(second.bundle_id)
        page.wait_for_timeout(1400)  # The first task's held response has now resolved.
        expect(page.locator("#viewer .target-hit")).to_have_count(0)
        expect(page.locator("#viewer")).to_contain_text("Select “Open source”")
        # The new task's own clicks navigate normally, with its own title and back link.
        link = page.locator("[data-atom-row]").filter(has_text="“1,150”").locator(".atom-link")
        link.click()
        expect(page.locator("#viewer .target-hit")).to_contain_text("1,150")
        page.locator("#viewer").get_by_role("button", name="Back to row").click()
        expect(link).to_be_focused()
        assert not errors
        ctx.close()


# ---------------------------------------------------------------- I29


def assigned_single():
    """The auditor's A09 input: assigned coverage, one assigned margin quantity, two numbers as context."""
    b = html_bundle()
    d = b.model_dump(mode="json")
    chosen = next(s for s in b.spans if s.text == "24.6%")
    d["form"] = [f for f in d["form"] if not f.get("numeric_span_id") and not f.get("atom")]
    d["form"].append(dict(field_id="assigned:margin", label="Checked assigned margin only", kind="boolean",
                          required=False, subject_id="margin", numeric_span_id=chosen.span_id))
    b = validate_bundle(d)
    atoms = build_atom_manifest(b, coverage="assigned", assigned_span_ids=[chosen.span_id])
    return b, atoms, prepare_render_asset(b, "filing", OriginalAsset(read("table.html"), "text/html"), atoms)


def test_one_assigned_atom_has_exactly_one_checkbox_that_saves_reloads_and_clears(tmp_path, browser):
    b, atoms, asset = assigned_single()
    workload = {"answer_index": 1, "assigned_answers": 3, "task_noun": "Check"}
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC, workload=workload) as h:
        ctx, page, errors = opened(browser, h)
        expect(page.locator("#workload-summary")).to_have_text("Check 1 of 3")
        bound = page.locator('input[data-quantity-field="assigned:margin"]')
        expect(bound).to_have_count(1)
        expect(page.locator("#items input[data-quantity-field]")).to_have_count(0)
        expect(page.locator("[data-atom-row]")).to_have_count(1)
        expect(page.locator("#report")).to_contain_text("2 other numbers are context only")
        with page.expect_response(lambda r: r.url.endswith("/api/save")) as saved:
            bound.check()
        assert saved.value.ok
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.reload()
        expect(page.locator("#status")).to_contain_text("Saved locally")
        expect(bound).to_have_count(1)
        expect(bound).to_be_checked()
        with page.expect_response(lambda r: r.url.endswith("/api/save")):
            bound.uncheck()
        page.reload()
        expect(page.locator("#status")).to_contain_text("Saved locally")
        expect(bound).not_to_be_checked()
        assert not errors
        ctx.close()


def test_legacy_modes_keep_their_statement_checks_and_default_noun(tmp_path, browser):
    b, _, _ = assigned_single()
    with open_review(b, FileStore(tmp_path), launch=False, workload={"answer_index": 2, "assigned_answers": 3}) as h:
        ctx, page, _ = opened(browser, h)
        expect(page.locator("#workload-summary")).to_have_text("Task 2 of 3")  # reference task default
        expect(page.locator("#items input[data-quantity-field='assigned:margin']")).to_have_count(1)
        ctx.close()
