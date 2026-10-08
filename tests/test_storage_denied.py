"""Audit regression (alpha-engine-config-I12167, denied tab storage); every value is synthetic.

Tab storage is optional. Whether the browser denies the ``sessionStorage``
accessor, ``getItem`` or ``setItem``, the launch fragment is scrubbed, the
token authenticates from memory, review works, and a reload without a token
shows a visible safe recovery instead of hanging on "Loading…".
"""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from rendered_fixtures import html_bundle, html_sidecars

ATOMIC = "atomic-source-check/v1"
DENY = {
    "accessor": "Object.defineProperty(window, 'sessionStorage', {get() { throw new DOMException('denied', 'SecurityError'); }});",
    "getItem": "Storage.prototype.getItem = function () { throw new DOMException('denied', 'SecurityError'); };",
    "setItem": "Storage.prototype.setItem = function () { throw new DOMException('denied', 'QuotaExceededError'); };",
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.mark.parametrize("mode", sorted(DENY))
def test_denied_tab_storage_keeps_review_usable_and_scrubs_the_launch_token(tmp_path, browser, mode):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    store = FileStore(tmp_path)
    with open_review(b, store, launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        ctx = browser.new_context()
        ctx.add_init_script(DENY[mode])
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        assert "#" not in page.url and "token" not in page.url
        expect(page.locator("[data-atom-row]")).to_have_count(4)
        page.locator("#assessor").fill("Ada")  # The assessor preference is optional storage too.
        row = page.locator("[data-atom-row]").filter(has_text="“24.6%”")
        row.get_by_role("button", name="Open the source").click()
        expect(page.locator("#viewer .target-hit")).to_have_count(1)
        check = row.locator("input[type=checkbox]")
        expect(check).not_to_be_checked()
        with page.expect_response(lambda r: r.url.endswith("/api/save")) as saved:
            check.check()
        assert saved.value.ok
        page.locator("#field-verdict\\:margin").select_option("cannot_verify")
        page.get_by_label("Explanation: Source verdict").fill("Synthetic denied-storage check.")
        page.locator("#field-report_complete").check()
        with page.expect_response(lambda r: r.url.endswith("/api/submit")) as submitted:
            page.locator("#submit").click()
        assert submitted.value.ok, submitted.value.text()
        assert store.load_task(b.bundle_id)["answers"]["judgments"][check.get_attribute("data-quantity-field")]["value"] is True
        # The token was never written anywhere a reload can read it, so recovery is explicit and safe.
        page.reload()
        expect(page.locator("#status")).to_contain_text("Reopen this review from the launch link")
        expect(page.locator("#status")).not_to_contain_text("Loading")
        assert "#" not in page.url
        assert not errors, errors
        ctx.close()


def test_available_tab_storage_still_survives_reload(tmp_path, browser):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(h.url)
        expect(page.locator("#status")).to_contain_text("Saved locally")
        assert "#" not in page.url
        page.reload()
        expect(page.locator("#status")).to_contain_text("Saved locally")
        ctx.close()


def test_opened_without_a_token_requests_nothing_and_shows_recovery(tmp_path, browser):
    b = html_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        ctx = browser.new_context()
        page = ctx.new_page()
        requests = []
        page.on("request", lambda r: requests.append(r.url) if "/api/" in r.url else None)
        page.goto(h.url.split("#")[0])
        expect(page.locator("#status")).to_contain_text("opened without its launch token")
        assert requests == []
        ctx.close()


def test_rejected_token_shows_recovery_not_a_raw_error(tmp_path, browser):
    b = html_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(h.url.split("#")[0] + "#token=" + "0" * 43)
        expect(page.locator("#status")).to_contain_text("did not accept this tab's launch token")
        assert "#" not in page.url
        ctx.close()
