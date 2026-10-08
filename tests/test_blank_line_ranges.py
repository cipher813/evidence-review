"""A cited range that spans blank frozen lines still maps to one exact target (real-corpus navigation defect).

The line-faithful renderer (``normalized_snapshot``, ``faithful_markdown``) gives a blank frozen line no node,
so a multi-line citation such as L3-L6 with L4-L5 empty used to be ``unavailable`` although every line that
carries text is rendered. Blank lines carry nothing to highlight, so they no longer break exactness: the target
covers the nodes of the non-blank lines, in order. A range that starts or ends on a blank line is trimmed to its
non-blank interior; a range of blank lines only has nothing to show and stays ``unavailable``. Forgeries of such
targets are still refused. Every value is synthetic.
"""
import copy

import pytest

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import RenderedSourceTarget, SourceRenderManifest, target_identity
from evidence_review.contracts import Citation, Claim, digest
from evidence_review.source_rendering import (
    OriginalAsset,
    RenderedSourceAsset,
    _Budget,
    _line_targets,
    _md_line_tree,
    RenderLimits,
    prepare_render_asset,
    render_frozen_text,
    validate_render_asset,
)
from synthetic import build, source

ATOMIC = "atomic-source-check/v1"
NOT_CANONICAL = "does not correspond to the frozen source"
UNPROVEN = "does not prove"

TEXT = "\n".join([
    "# Results",                                 # 1
    "",                                          # 2
    "Revenue was $412 million in FY2025.",       # 3
    "",                                          # 4
    "   ",                                       # 5  whitespace only
    "Operating margin improved to 24.6%.",       # 6
    "## Segments",                               # 7
    "",                                          # 8
    "| Segment | FY2025 |",                      # 9
    "| --- | --- |",                             # 10
    "| Retail | 31.2% |",                        # 11
    "",                                          # 12
    "- Retail grew 8%.",                         # 13
    "",                                          # 14
    "- Wholesale grew 3%.",                      # 15
    "",                                          # 16
    "Closing note.",                             # 17
    "",                                          # 18
]) + "\n"


def cite(start, end, excerpt):
    return Citation(source_id="filing", start_line=start, end_line=end, excerpt=excerpt, status="located")


CITES = {
    "prose_one_blank": cite(1, 3, "Results"),
    "prose_several_blanks": cite(3, 6, "24.6%"),
    "excerpt_across_blanks": cite(3, 6, "in FY2025. Operating margin"),
    "heading_to_table": cite(7, 11, "31.2%"),
    "inside_list": cite(13, 15, "Wholesale grew 3%."),
    "starts_on_blank": cite(2, 3, "$412 million"),
    "ends_on_blank": cite(11, 12, "31.2%"),
    "both_ends_blank": cite(12, 14, "Retail grew 8%."),
    "trailing_blank": cite(17, 18, "Closing note."),
}


def bundle(mode):
    return build([("summary", "Margin was 24.6%.", ["c"])],
                 [Claim(claim_id="c", text="x", citations=list(CITES.values()))],
                 sources=[source(TEXT)], bundle_id="blank-lines-" + mode.replace("_", "-"))


def rendered(mode):
    b = bundle(mode)
    asset = (render_frozen_text(b, "filing") if mode == "normalized_snapshot"
             else prepare_render_asset(b, "filing", OriginalAsset(TEXT.encode(), "text/markdown")))
    assert asset.manifest.render_mode == mode
    return b, asset


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children", []))


def by_line(derivative):
    return {n["line"]: n for n in walk(derivative["nodes"]) if "line" in n}


def target(asset, name):
    c = CITES[name]
    return next(t for t in asset.manifest.targets
                if (t.start_line, t.end_line, t.excerpt) == (c.start_line, c.end_line, c.excerpt))


def rehashed(asset, mutate_derivative=None, mutate_targets=None):
    derivative = copy.deepcopy(asset.derivative)
    if mutate_derivative:
        mutate_derivative(derivative)
    m = asset.manifest.model_dump(mode="json")
    if mutate_targets:
        mutate_targets(m["targets"])
    m["derivative_sha256"] = digest(derivative)
    m["mapping_sha256"] = digest(m["targets"])
    return RenderedSourceAsset(SourceRenderManifest.model_validate(m), derivative)


def refused(b, asset, tmp_path, match):
    with pytest.raises(ValueError, match=match):
        validate_render_asset(b, asset)
    with pytest.raises(ValueError, match=match):
        open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC)
    assert not (tmp_path / b.bundle_id).exists()


MODES = ["normalized_snapshot", "faithful_markdown"]

# Non-blank lines each cited range must highlight, in order (the separator line maps to the header row).
EXPECTED_LINES = {
    "prose_one_blank": [1, 3],
    "prose_several_blanks": [3, 6],
    "excerpt_across_blanks": [3, 6],
    "heading_to_table": [7, 9, 11],
    "inside_list": [13, 15],
    "starts_on_blank": [3],
    "both_ends_blank": [13],
    "trailing_blank": [17],
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", sorted(EXPECTED_LINES))
def test_range_spanning_blank_lines_is_exact_over_its_non_blank_nodes(mode, name):
    b, asset = rendered(mode)
    t = target(asset, name)
    assert t.status == "exact", t.reason
    lines = by_line(asset.derivative)
    assert t.dom_targets == [lines[n]["id"] for n in EXPECTED_LINES[name]]
    assert t.reason == ""
    assert validate_render_asset(b, asset).manifest == asset.manifest


@pytest.mark.parametrize("mode", MODES)
def test_range_ending_on_a_blank_after_a_table_row_is_trimmed_to_the_exact_cell(mode):
    b, asset = rendered(mode)
    t = target(asset, "ends_on_blank")
    assert t.status == "exact"
    row = by_line(asset.derivative)[11]
    (cell,) = [c for c in row["children"] if c["text"] == "31.2%"]
    assert t.dom_targets == [cell["id"]]


@pytest.mark.parametrize("mode", MODES)
def test_every_target_survives_the_protected_routes(tmp_path, mode):
    b, asset = rendered(mode)
    assert all(t.status == "exact" for t in asset.manifest.targets)
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        assert h.origin  # Opening re-proves every exact target against the canonical rendering.


def _targets(citations, text=TEXT):
    src = source(text)
    nodes, line_nodes, cells = _md_line_tree(src.text, _Budget(RenderLimits()), [])
    return _line_targets(src, citations, line_nodes, cells, None)


@pytest.mark.parametrize("span", [(4, 5), (2, 2), (16, 16), (18, 18)])
def test_range_of_blank_lines_only_stays_unavailable_with_a_reason(span):
    (t,) = _targets([cite(*span, "x")])
    assert t.status == "unavailable" and not t.dom_targets
    assert "blank" in t.reason


def test_excerpt_across_the_blank_line_is_normalised_like_the_frozen_citation_check():
    (good, multi_space, wrong) = _targets([cite(3, 6, "FY2025.\n\n\nOperating"),
                                           cite(3, 6, "FY2025.    Operating   margin"),
                                           cite(3, 6, "FY2025. Revenue")])
    assert good.status == multi_space.status == "exact"
    assert wrong.status == "unavailable" and "excerpt" in wrong.reason.lower()


def test_line_absent_from_a_non_blank_range_is_still_unavailable():
    (t,) = _targets([cite(17, 19, "Closing note.")])
    assert t.status == "unavailable"


# ---------------------------------------------------------------- forgeries stay refused


@pytest.mark.parametrize("mode", MODES)
def test_changed_text_on_a_line_after_the_blank_is_refused(tmp_path, mode):
    b, asset = rendered(mode)
    t = target(asset, "prose_several_blanks")
    six = by_line(asset.derivative)[6]
    assert six["id"] in t.dom_targets

    def forge(d):
        by_line(d)[6]["text"] = "Operating margin improved to 42.6%."
    refused(b, rehashed(asset, forge), tmp_path, NOT_CANONICAL)


@pytest.mark.parametrize("mode", MODES)
def test_node_inserted_for_a_blank_line_is_refused(tmp_path, mode):
    b, asset = rendered(mode)

    def forge(d):
        para = next(n for n in d["nodes"] if n["tag"] == "p" and n["children"][0].get("line") == 3)
        para["children"].append({"id": "n999", "tag": "line", "line": 4, "text": "Revenue was restated."})
    refused(b, rehashed(asset, forge), tmp_path, NOT_CANONICAL)


@pytest.mark.parametrize("mode", MODES)
def test_target_dropping_a_non_blank_line_is_refused(tmp_path, mode):
    b, asset = rendered(mode)
    t = target(asset, "heading_to_table")

    def forge(targets):
        x = next(x for x in targets if x["target_id"] == t.target_id)
        x["dom_targets"] = x["dom_targets"][1:]
    refused(b, rehashed(asset, mutate_targets=forge), tmp_path, UNPROVEN)


@pytest.mark.parametrize("mode", MODES)
def test_all_blank_range_forged_exact_is_refused(tmp_path, mode):
    b, asset = rendered(mode)
    some = by_line(asset.derivative)[3]["id"]
    forged = RenderedSourceTarget(
        target_id=target_identity("filing", asset.manifest.frozen_sha256, 4, 5, "Revenue"),
        source_id="filing", frozen_sha256=asset.manifest.frozen_sha256, raw_sha256=asset.manifest.raw_sha256,
        start_line=4, end_line=5, excerpt="Revenue", dom_targets=[some], status="exact")
    refused(b, rehashed(asset, mutate_targets=lambda ts: ts.append(forged.model_dump(mode="json"))),
            tmp_path, UNPROVEN)


# ---------------------------------------------------------------- one click in Chromium


def test_chromium_click_highlights_every_non_blank_line_of_a_blank_spanning_range(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    from evidence_review.contracts import validate_bundle

    c = CITES["prose_several_blanks"]
    b = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="Margin was 24.6%.", citations=[c])],
              sources=[source(TEXT)], task_kind="reference", bundle_id="blank-lines-browser")
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        s.update(state="cited", claim_ids=["c"], citations=[c.model_dump(mode="json")])
    b = validate_bundle(data)
    asset = render_frozen_text(b, "filing")
    (t,) = asset.manifest.targets
    assert t.status == "exact" and len(t.dom_targets) == 2
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset],
                         presentation_mode=ATOMIC) as h:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(h.url)
            expect(page.locator("#status")).to_contain_text("Saved locally")
            page.locator(".atom-link").first.click()
            expect(page.locator("#viewer .target-status")).to_contain_text("Exact location highlighted")
            hits = page.locator("#viewer .target-hit")
            expect(hits).to_have_count(2)
            assert [hits.nth(k).get_attribute("data-line") for k in range(2)] == ["3", "6"]
            expect(hits.nth(0)).to_contain_text("Revenue was $412 million in FY2025.")
            expect(hits.nth(1)).to_contain_text("Operating margin improved to 24.6%.")
            expect(hits.nth(0)).to_be_in_viewport()
            assert page.locator("#viewer [data-line='4'], #viewer [data-line='5']").count() == 0
            assert not errors
        browser.close()
