"""A caller-supplied render manifest cannot claim an exact location its own derivative does not prove (0.5.4)."""
import copy

import pytest

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import SourceRenderManifest
from evidence_review.contracts import digest
from evidence_review.source_rendering import (
    OriginalAsset,
    RenderedSourceAsset,
    prepare_render_asset,
    render_frozen_text,
    validate_render_asset,
)
from rendered_fixtures import html_bundle, html_sidecars, read
from synthetic import build, located, source
from evidence_review.contracts import Claim, validate_bundle

ATOMIC = "atomic-source-check/v1"


def forged(asset, change, derivative=None):
    """Re-sign a changed mapping so only the node proof can catch it (hashes are self-consistent)."""
    m = asset.manifest.model_dump(mode="json")
    for t in m["targets"]:
        change(t)
    m["mapping_sha256"] = digest(m["targets"])
    derivative = derivative if derivative is not None else asset.derivative
    m["derivative_sha256"] = digest(derivative)
    return RenderedSourceAsset(SourceRenderManifest.model_validate(m), derivative)


def node_ids(derivative, predicate):
    out = []

    def walk(nodes):
        for n in nodes:
            if predicate(n):
                out.append(n["id"])
            walk(n.get("children", []))
    walk(derivative["nodes"])
    return out


def refused(b, asset, tmp_path, match):
    with pytest.raises(ValueError, match=match):
        validate_render_asset(b, asset)
    with pytest.raises(ValueError, match=match):
        open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC)
    assert not (tmp_path / b.bundle_id).exists()


def test_exact_target_naming_a_missing_node_is_refused(tmp_path):
    b = html_bundle()
    _, asset = html_sidecars(b)
    bad = forged(asset, lambda t: t.update(dom_targets=["n99999"]) if t["status"] == "exact" else None)
    refused(b, bad, tmp_path, "absent from the rendered derivative")


def test_exact_target_on_unrelated_or_same_text_wrong_row_node_is_refused(tmp_path):
    b = html_bundle()
    _, asset = html_sidecars(b)
    first = asset.derivative["nodes"][0]["id"]  # the page heading: unrelated text
    bad = forged(asset, lambda t: t.update(status="exact", dom_targets=[first], candidates=[], reason="")
                 if t["excerpt"] == "24.6%" else None)
    refused(b, bad, tmp_path / "unrelated", "does not prove")
    # The same 24.6% under "Retail margin" holds the right text but the wrong row (metric): still not exact.
    cells = node_ids(asset.derivative, lambda n: n["tag"] == "td" and n.get("children") and
                     "24.6%" in str(n["children"]))
    proven = next(t for t in asset.manifest.targets if t.excerpt == "24.6%" and t.status == "exact")
    other = next(c for c in cells if c not in proven.dom_targets)
    bad = forged(asset, lambda t: t.update(dom_targets=[other]) if t["target_id"] == proven.target_id else None)
    refused(b, bad, tmp_path / "wrong-row", "does not prove")


def test_exact_line_target_on_another_line_is_refused(tmp_path):
    text = "Revenue was 24.6% higher.\nMargin was 24.6%.\n"
    b = validate_bundle(build([("summary", "Margin was 24.6%.", ["c"])],
                              [Claim(claim_id="c", text="Margin was 24.6%.", citations=[located(2, "24.6%")])],
                              sources=[source(text)], task_kind="reference", bundle_id="lines")
                        .model_dump(mode="json"))
    asset = render_frozen_text(b, "filing")
    (t,) = asset.manifest.targets
    line1 = node_ids(asset.derivative, lambda n: n.get("line") == 1)
    assert t.status == "exact" and t.dom_targets != line1
    refused(b, forged(asset, lambda x: x.update(dom_targets=line1)), tmp_path, "does not prove")


def test_pdf_exact_target_with_wrong_page_or_box_and_absent_page_is_refused(tmp_path):
    raw = read("two-page.pdf")
    b = build([("summary", "Margin was 24.6%.", ["c"])],
              [Claim(claim_id="c", text="x", citations=[located(3, "24.6%")])],
              sources=[source(read("two-page.frozen.txt").decode())], bundle_id="pdf-proof")
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "application/pdf"))
    t = next(x for x in asset.manifest.targets if x.status == "exact")
    refused(b, forged(asset, lambda x: x.update(page=2) if x["target_id"] == t.target_id else None),
            tmp_path / "page", "does not prove")
    refused(b, forged(asset, lambda x: x.update(bbox=[0.0, 0.0, 1.0, 1.0]) if x["target_id"] == t.target_id
                      else None), tmp_path / "box", "does not prove")
    refused(b, forged(asset, lambda x: x.update(status="page_only", page=7, dom_targets=[], reason="caller page")),
            tmp_path / "absent", "page absent")


def test_ambiguous_candidate_without_the_excerpt_and_foreign_derivative_are_refused(tmp_path):
    b = html_bundle()
    _, asset = html_sidecars(b)
    heading = asset.derivative["nodes"][0]["id"]
    cells = node_ids(asset.derivative, lambda n: n["tag"] == "td")
    bad = forged(asset, lambda t: t.update(status="ambiguous", dom_targets=[], candidates=[[heading], [cells[0]]],
                                           reason="caller says two") if t["excerpt"] == "24.6%" else None)
    refused(b, bad, tmp_path / "cand", "does not hold its excerpt")
    other = copy.deepcopy(asset.derivative)
    other["render_mode"] = "normalized_snapshot"
    refused(b, forged(asset, lambda t: None, other), tmp_path / "mode", "another source or render mode")


def test_every_package_rendering_proves_its_own_exact_targets_and_downgrades_are_allowed(tmp_path):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    assert validate_render_asset(b, asset).manifest == asset.manifest
    assert any(t.status == "exact" for t in asset.manifest.targets)
    for name, media in [("table.frozen.md", "text/markdown"), ("escaped.md", "text/markdown")]:
        text = read(name).decode()
        cites = [located(i + 1, line.split()[0]) for i, line in enumerate(text.splitlines())
                 if line.strip() and line.split()[0] not in ("|", "#")][:6]
        bb = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=cites)],
                   sources=[source(text)], bundle_id="own-" + name.replace(".", "-"))
        own = prepare_render_asset(bb, "filing", OriginalAsset(text.encode(), media))
        assert validate_render_asset(bb, own).manifest == own.manifest
    # Claiming LESS than proven (exact -> unavailable) is an honest limitation and stays accepted.
    weaker = forged(asset, lambda t: t.update(status="unavailable", dom_targets=[], candidates=[],
                                              reason="caller withholds the location"))
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[weaker],
                     presentation_mode=ATOMIC):
        pass
