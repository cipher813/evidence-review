"""Blank Markdown headers are structural table cells, not inferred semantic labels.

All values are synthetic. Conventional empty corner cells and sparse headers must
render as bounded rectangular tables while frozen line/cell targets remain exact.
Semantic quantity/header inference retains its stricter eligibility rules.
"""
import copy

import pytest

from evidence_review.contracts import Citation, Claim, digest
from evidence_review.source_rendering import (
    OriginalAsset, RenderedSourceAsset, prepare_render_asset, render_frozen_text, validate_render_asset,
)
from evidence_review.atomic_evidence import SourceRenderManifest
from evidence_review.table_context import table_context
from synthetic import build, located, source


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children", []))


def asset_for(text, mode, citations=()):
    bundle = build([("summary", "Revenue was 42.", ["revenue"])],
                   [Claim(claim_id="revenue", text="Revenue was 42.", citations=list(citations))],
                   sources=[source(text)], bundle_id="empty-header-" + mode.replace("_", "-"))
    asset = (render_frozen_text(bundle, "filing") if mode == "normalized_snapshot"
             else prepare_render_asset(bundle, "filing", OriginalAsset(text.encode(), "text/markdown")))
    return bundle, asset


@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
@pytest.mark.parametrize("header", ["| | FY2025 | FY2026 |", "| | FY2025 | |"])
def test_empty_headers_render_without_inventing_labels_or_shifting_cells(mode, header):
    text = "Units: USD millions\n" + header + "\n| --- | --- | --- |\n| Revenue | 42 | 11 |\n| Margin | 42 | 12 |\n"
    bundle, asset = asset_for(text, mode, [located(4, "42"), located(5, "42")])
    original = bundle.model_dump(mode="json")
    tables = [n for n in walk(asset.derivative["nodes"]) if n["tag"] == "table"]
    assert len(tables) == 1, asset.manifest.transforms
    table = tables[0]
    rows = [n for n in walk([table]) if n["tag"] == "tr"]
    assert [r["line"] for r in rows] == [2, 4, 5]
    assert [c["text"] for c in rows[0]["children"]] == (
        ["", "FY2025", "FY2026"] if header.endswith("FY2026 |") else ["", "FY2025", ""])
    assert [c["text"] for c in rows[1]["children"]] == ["Revenue", "42", "11"]
    assert [c["text"] for c in rows[2]["children"]] == ["Margin", "42", "12"]
    by_id = {n["id"]: n for n in walk(asset.derivative["nodes"])}
    targets = sorted(asset.manifest.targets, key=lambda t: t.start_line)
    assert [(t.start_line, t.end_line, t.excerpt, t.status) for t in targets] == [
        (4, 4, "42", "exact"), (5, 5, "42", "exact")]
    assert targets[0].dom_targets != targets[1].dom_targets
    assert targets[0].dom_targets == [rows[1]["children"][1]["id"]]
    assert targets[1].dom_targets == [rows[2]["children"][1]["id"]]
    assert all(by_id[t.dom_targets[0]]["text"] == "42" for t in targets)
    assert validate_render_asset(bundle, asset).manifest == asset.manifest
    assert bundle.model_dump(mode="json") == original
    assert bundle.sources[0].text == text
    # Rendering cell positions does not prove an unnamed column's semantic identity.
    context = table_context(bundle.sources[0], 4, 5)
    assert context["status"] == "unparsed"
    assert context["headers"] == [] and context["rows"] == []


@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
def test_sparse_six_column_header_preserves_every_blank_cell_and_range_target(mode):
    text = ("Heading\n| | Prior quarter | | Current quarter | Change | |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Revenue | 31 | | 42 | 11 | |\n| Margin | 12 | | 14 | 2 | |\n")
    citation = Citation(source_id="filing", start_line=2, end_line=5, excerpt="42", status="located")
    bundle, asset = asset_for(text, mode, [citation])
    tables = [n for n in walk(asset.derivative["nodes"]) if n["tag"] == "table"]
    assert len(tables) == 1, asset.manifest.transforms
    table = tables[0]
    rows = [n for n in walk([table]) if n["tag"] == "tr"]
    assert [c["text"] for c in rows[0]["children"]] == ["", "Prior quarter", "", "Current quarter", "Change", ""]
    assert [c["text"] for c in rows[1]["children"]] == ["Revenue", "31", "", "42", "11", ""]
    assert [c["text"] for c in rows[2]["children"]] == ["Margin", "12", "", "14", "2", ""]
    (target,) = asset.manifest.targets
    assert target.status == "exact"
    # The separator maps to its header, not a duplicate or fabricated row.
    assert target.dom_targets == [r["id"] for r in rows]
    validate_render_asset(bundle, asset)


@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
def test_rehashed_invented_header_is_refused_against_frozen_empty_cell(mode):
    text = "| | FY2025 |\n| --- | --- |\n| Revenue | 42 |\n"
    bundle, asset = asset_for(text, mode, [located(3, "42")])
    derivative = copy.deepcopy(asset.derivative)
    heads = [n for n in walk(derivative["nodes"]) if n["tag"] == "tr" and n["line"] == 1]
    assert len(heads) == 1, asset.manifest.transforms
    head = heads[0]
    head["children"][0]["text"] = "Invented metric label"
    manifest = asset.manifest.model_dump(mode="json")
    manifest["derivative_sha256"] = digest(derivative)
    forged = RenderedSourceAsset(SourceRenderManifest.model_validate(manifest), derivative)
    with pytest.raises(ValueError, match="does not correspond to the frozen source"):
        validate_render_asset(bundle, forged)


@pytest.mark.parametrize("text", [
    "| | FY2025 |\n| --- | --- |\n| Revenue | 42 | 11 |\n",
    "| | FY2025 |\n| -- | --- |\n| Revenue | 42 |\n",
    "| | FY2025 |\n| --- | --- |\n| `Revenue|other` | 42 |\n",
])
def test_empty_headers_do_not_relax_width_separator_or_unsupported_syntax_checks(text):
    bundle, asset = asset_for(text, "normalized_snapshot")
    assert not any(n["tag"] == "table" for n in walk(asset.derivative["nodes"]))
    assert any(n["tag"] == "pre" for n in walk(asset.derivative["nodes"]))
    validate_render_asset(bundle, asset)
