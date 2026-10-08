"""Table header association follows the occupied-cell grid, not DOM cell order (alpha-engine-config-I12170 P2).

A cell's column and row headers are read from a grid that respects rowSpan and colSpan across rows in
thead, tbody and tfoot; explicit ``headers``/``id`` win, then ``scope``, then grid position. A nested
table uses only its own grid. Where the association is missing or ambiguous the context says
"unavailable", visibly and to assistive technology; a period is never invented. Every value is synthetic."""
import pytest
from playwright.sync_api import expect, sync_playwright

from evidence_review import FileStore, open_review
from evidence_review.contracts import Claim, validate_bundle
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset
from synthetic import build, located, source

ATOMIC = "atomic-source-check/v1"

# The auditor's reproduction: "Metric" spans two header rows, "Retail" spans two columns.
MULTIROW_HEAD = """<table><caption>Table 2. Retail segment by fiscal year</caption>
<thead><tr><th rowspan="2">Metric</th><th colspan="2">Retail</th></tr>
<tr><th>FY2025</th><th>FY2026</th></tr></thead>
<tbody><tr><th>Operating margin</th><td>22.8%</td><td>24.6%</td></tr></tbody>
<tfoot><tr><td colspan="3">Margins in percent of segment revenue.</td></tr></tfoot></table>
<p>(1) FY2025 restated for a disposed store group.</p>"""

# A body rowspan: the second body row's first DOM cell sits in grid column 1, not 0.
BODY_ROWSPAN = """<table><thead><tr><th>Segment</th><th>Metric</th><th>FY2025</th><th>FY2026</th></tr></thead>
<tbody><tr><th rowspan="2">Retail</th><th>Operating margin</th><td>22.8%</td><td>24.6%</td></tr>
<tr><th>Net margin</th><td>9.1%</td><td>10.4%</td></tr></tbody></table>"""

# Explicit headers/id contradict what grid position alone would say.
EXPLICIT = """<table><thead><tr><th id="m">Metric</th><th id="a">FY2025</th><th id="b">FY2026</th></tr></thead>
<tbody><tr><th id="om">Operating margin</th><td headers="om b">22.8%</td><td headers="om a">24.6%</td></tr>
</tbody></table>"""

# A nested table: the inner cell must use only the inner table's grid.
NESTED = """<table><thead><tr><th>Region</th><th>Detail</th></tr></thead>
<tbody><tr><th>North</th><td><table><thead><tr><th>Metric</th><th>FY2027</th></tr></thead>
<tbody><tr><th>Operating margin</th><td>22.8%</td></tr></tbody></table></td></tr></tbody></table>"""

# Ambiguous: a header row whose two cells both claim the target's column; and an unresolvable headers=.
AMBIGUOUS = """<table><thead><tr><th>Metric</th><th colspan="2">Retail</th></tr>
<tr><th></th><th colspan="2">FY2025</th></tr>
<tr><th></th><th>FY2025</th><th>FY2026</th></tr></thead>
<tbody><tr><th>Operating margin</th><td colspan="2">22.8%</td></tr></tbody></table>"""
MISSING = """<table><tbody><tr><td>Metric</td><td>Value</td></tr>
<tr><td>Operating margin</td><td headers="nowhere">22.8%</td></tr></tbody></table>"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def review(tmp_path, table_html, frozen_row="Operating margin 22.8%", excerpt="22.8%"):
    """A one-citation source check whose HTML original holds ``table_html``; the frozen text cites one row."""
    frozen = "Results\n" + frozen_row + "\n"
    cite = located(2, excerpt)
    statement = f"Margin was {excerpt}."
    b = build([("summary", statement, ["c"])], [Claim(claim_id="c", text=statement, citations=[cite])],
              sources=[source(frozen)], task_kind="reference", bundle_id="grid")
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="cited", claim_ids=["c"], citations=[cite.model_dump(mode="json")])
    b = validate_bundle(data)
    html = f"<html><body><h2>Results</h2>{table_html}</body></html>".encode()
    asset = prepare_render_asset(b, "filing", OriginalAsset(html, "text/html"))
    assert asset.manifest.targets[0].status == "exact", asset.manifest.targets[0]
    return open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC)


def open_cell(page, h, value):
    page.goto(h.url)
    expect(page.locator("#status")).to_contain_text("Saved locally")
    expect(page.locator("#status")).to_contain_text("revision 0")
    page.locator(".atom-link").first.click()
    hit = page.locator("#viewer .target-hit")
    expect(hit).to_have_count(1)
    expect(hit).to_contain_text(value)
    context = page.locator("#viewer .target-context")
    expect(context).to_be_visible()
    # The accessible description is the same visible context, and navigation saved nothing.
    assert hit.get_attribute("aria-describedby") == "target-context"
    described = hit.evaluate("n => document.getElementById(n.getAttribute('aria-describedby')).textContent")
    assert described == context.text_content()
    expect(page.locator("#status")).to_contain_text("revision 0")
    return hit, context.text_content()


def test_auditor_reproduction_multirow_multicolumn_head_names_fy2025(tmp_path, browser):
    with review(tmp_path, MULTIROW_HEAD, "Operating margin 22.8% 24.6%") as h:
        page = browser.new_page()
        hit, text = open_cell(page, h, "22.8%")
        assert "Column: Retail / FY2025" in text, text
        assert "FY2026" not in text, text
        assert "Row: Operating margin" in text
        assert "Caption: Table 2. Retail segment by fiscal year" in text
        assert "Table note: Margins in percent of segment revenue." in text
        assert "Note: (1) FY2025 restated for a disposed store group." in text
        page.close()


def test_body_rowspan_shifts_later_rows_columns(tmp_path, browser):
    with review(tmp_path, BODY_ROWSPAN, "Net margin 9.1% 10.4%", "10.4%") as h:
        page = browser.new_page()
        _, text = open_cell(page, h, "10.4%")
        assert "Column: FY2026" in text, text
        assert "Row: Retail / Net margin" in text, text
        assert "FY2025" not in text
        page.close()


def test_explicit_headers_attribute_wins_over_grid_position(tmp_path, browser):
    with review(tmp_path, EXPLICIT, "Operating margin 22.8% 24.6%") as h:
        page = browser.new_page()
        _, text = open_cell(page, h, "22.8%")
        assert "Column: FY2026" in text, text
        assert "Row: Operating margin" in text
        assert "FY2025" not in text
        page.close()


def test_nested_table_cell_uses_only_the_inner_grid(tmp_path, browser):
    with review(tmp_path, NESTED) as h:
        page = browser.new_page()
        _, text = open_cell(page, h, "22.8%")
        assert "Column: FY2027" in text, text
        assert "Row: Operating margin" in text, text
        for outer in ("Detail", "Region", "North"):
            assert outer not in text, text
        page.close()


def test_rowspan_ends_at_its_row_group_so_tfoot_is_not_shifted(tmp_path, browser):
    table = BODY_ROWSPAN.replace('rowspan="2"', 'rowspan="5"').replace(
        "</tbody>", "</tbody><tfoot><tr><th>Total</th><th>All</th><td>30.0%</td><td>31.0%</td></tr></tfoot>")
    with review(tmp_path, table, "Total All 30.0% 31.0%", "31.0%") as h:
        page = browser.new_page()
        _, text = open_cell(page, h, "31.0%")
        assert "Column: FY2026" in text, text
        assert "Row: Total / All" in text, text
        assert "Retail" not in text.split("Table note")[0], text
        page.close()


@pytest.mark.parametrize("table", [AMBIGUOUS, MISSING], ids=["two-headers-one-level", "unresolved-headers"])
def test_ambiguous_or_missing_association_reads_unavailable(tmp_path, browser, table):
    with review(tmp_path, table) as h:
        page = browser.new_page()
        _, text = open_cell(page, h, "22.8%")
        assert "Column: unavailable" in text, text
        assert "FY2025" not in text and "FY2026" not in text and "Value" not in text, text
        page.close()


def _cells(derivative):
    out = []

    def walk(ns):
        for n in ns:
            if n["tag"] in ("td", "th"):
                out.append(n)
            walk(n.get("children", []))
    walk(derivative["nodes"])
    return out


def _text(n):
    return n.get("text") or " ".join(_text(c) for c in n.get("children", []))


def test_server_scope_inference_uses_grid_position():
    """A th is a row header only when every grid position to its left holds a header cell."""
    html = b"""<html><body><table><thead><tr><th>Segment</th><th>Metric</th><th>FY2025</th></tr></thead><tbody>
<tr><th rowspan="2">Retail</th><th>Operating margin</th><td>22.8%</td></tr>
<tr><th>Net margin</th><td>9.1%</td></tr>
<tr><td rowspan="2">Other</td><td>Gross</td><td>1.0%</td></tr>
<tr><th>Unproven</th><td>2.0%</td></tr></tbody></table></body></html>"""
    b = build([("summary", "Margin was 22.8%.", ["c"])],
              [Claim(claim_id="c", text="Margin was 22.8%.", citations=[located(2, "22.8%")])],
              sources=[source("Results\nOperating margin 22.8%\n")], task_kind="reference", bundle_id="grid")
    asset = prepare_render_asset(b, "filing", OriginalAsset(html, "text/html"))
    scope = {_text(n): n.get("attrs", {}).get("scope") for n in _cells(asset.derivative)}
    assert scope["Retail"] == scope["Operating margin"] == scope["Net margin"] == "row"
    assert scope["Unproven"] is None  # grid column 1, behind a td spanning from above: not a row header


def test_server_keeps_header_associations_namespaced():
    html = (b"<html><body>" + EXPLICIT.encode()
            + b"<table><tr><td id='bad id!' headers='x  y'>q</td></tr></table></body></html>")
    b = build([("summary", "Margin was 22.8%.", ["c"])],
              [Claim(claim_id="c", text="Margin was 22.8%.", citations=[located(2, "22.8%")])],
              sources=[source("Results\nOperating margin 22.8% 24.6%\n")], task_kind="reference", bundle_id="grid")
    asset = prepare_render_asset(b, "filing", OriginalAsset(html, "text/html"))
    attrs = {_text(n): n.get("attrs", {}) for n in _cells(asset.derivative)}
    assert attrs["FY2026"]["data-cell-id"] == "b"
    assert attrs["22.8%"]["data-headers"] == "om b"
    assert all("id" not in a and "headers" not in a for a in attrs.values())
    assert attrs["q"] == {"data-headers": "x y"}  # an id outside the token grammar is dropped, not kept raw
