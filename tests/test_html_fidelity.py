"""HTML fidelity (0.5.4): permitted local styling survives sanitizing, and header cells carry the
screen-reader association their table position proves, even when the original omitted ``scope``."""
import json

import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, validate_bundle
from evidence_review.source_rendering import OriginalAsset, check_derivative, prepare_render_asset
from synthetic import build, located, source

FROZEN = ("Results\n| Metric | FY2025 | FY2026 |\n| --- | --- | --- |\n| Operating margin | 22.8% | 24.6% |\n"
          "| Revenue | 1,200 | 1,150 |\n")
HTML = b"""<html><body><p style="text-align:center; font-weight:bold; color:red">Results</p>
<table><thead><tr><th>Metric</th><th>FY2025</th><th style="text-align:right">FY2026</th></tr></thead>
<tbody><tr><th>Operating margin</th><td>22.8%</td><td style="text-align: RIGHT; background:url(https://x.invalid/p.gif);
 position:fixed; padding-left: 2em">24.6%</td></tr>
<tr><th scope="rowgroup">Revenue</th><td>1,200</td><th>1,150</th></tr></tbody></table>
<table><tr><th>Plain</th><th>Header</th></tr><tr><td>a</td><td>b</td></tr></table></body></html>"""


def bundle():
    cite = located(4, "24.6%")
    b = build([("summary", "Margin was 24.6%.", ["c"])],
              [Claim(claim_id="c", text="Margin was 24.6%.", citations=[cite])],
              sources=[source(FROZEN)], task_kind="reference", bundle_id="fidelity")
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="cited", claim_ids=["c"], citations=[cite.model_dump(mode="json")])
    return validate_bundle(data)


def nodes(derivative):
    out = []

    def walk(ns):
        for n in ns:
            out.append(n)
            walk(n.get("children", []))
    walk(derivative["nodes"])
    return out


def text(n):
    return n.get("text") or " ".join(text(c) for c in n.get("children", []))


def test_header_scope_is_inferred_only_where_structure_proves_it():
    asset = prepare_render_asset(bundle(), "filing", OriginalAsset(HTML, "text/html"))
    ths = {text(n): n.get("attrs", {}).get("scope") for n in nodes(asset.derivative) if n["tag"] == "th"}
    assert ths["Metric"] == ths["FY2025"] == ths["FY2026"] == "col"  # thead
    assert ths["Operating margin"] == "row"  # starts a body row
    assert ths["Revenue"] == "rowgroup"  # the original's own scope is kept
    assert ths["1,150"] is None  # a th mid-row proves nothing: not guessed
    assert ths["Plain"] == ths["Header"] == "col"  # first row of th without thead
    assert "added header scope inferred from table structure (6)" in asset.manifest.transforms
    assert all("scope" not in n.get("attrs", {}) for n in nodes(asset.derivative) if n["tag"] == "td")


def test_allowlisted_local_style_is_kept_and_everything_else_removed_and_declared():
    asset = prepare_render_asset(bundle(), "filing", OriginalAsset(HTML, "text/html"))
    styled = {text(n): n.get("style") for n in nodes(asset.derivative) if n.get("style")}
    assert styled == {"Results": {"text-align": "center", "font-weight": "bold"},
                      "FY2026": {"text-align": "right"},
                      "24.6%": {"text-align": "right", "padding-left": "2em"}}
    tree = json.dumps(asset.derivative)
    for gone in ("url(", "x.invalid", "color", "fixed", "background", "position"):
        assert gone not in tree, gone
    transforms = asset.manifest.transforms
    assert "kept allowlisted local style declaration (5)" in transforms
    assert "removed style declaration outside the allowlist (3)" in transforms
    assert next(t for t in asset.manifest.targets).status == "exact"
    for bad in ({"position": "fixed"}, {"text-align": "url(x)"}, {"margin-left": "-999px"}, "text-align:right"):
        with pytest.raises(ValueError, match="unsafe style"):
            check_derivative({"schema_version": "rendered-source/v1",
                              "nodes": [{"id": "s", "tag": "p", "text": "x", "style": bad}]})


def test_browser_applies_allowlisted_style_and_scope_without_weakening_csp(tmp_path):
    b = bundle()
    asset = prepare_render_asset(b, "filing", OriginalAsset(HTML, "text/html"))
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset],
                     presentation_mode="atomic-source-check/v1") as h, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors, csp = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: csp.append(m.text) if "Content Security Policy" in m.text else None)
        response = page.goto(h.url)
        assert "style-src 'self';" in response.headers["content-security-policy"]
        expect(page.locator("#status")).to_contain_text("Saved locally")
        page.locator(".atom-link").first.click()
        hit = page.locator("#viewer .target-hit")
        expect(hit).to_contain_text("24.6%")
        assert hit.evaluate("n => getComputedStyle(n).textAlign") == "right"
        assert hit.evaluate("n => getComputedStyle(n).paddingLeft") not in ("", "0px")
        expect(page.locator("#viewer th").filter(has_text="Operating margin")).to_have_attribute("scope", "row")
        expect(page.locator("#viewer th").filter(has_text="FY2026")).to_have_attribute("scope", "col")
        expect(page.locator("#viewer .target-context")).to_contain_text("Column: FY2026")
        expect(page.locator("#viewer .target-context")).to_contain_text("Row: Operating margin")
        assert not errors and not csp, (errors, csp)
        browser.close()
