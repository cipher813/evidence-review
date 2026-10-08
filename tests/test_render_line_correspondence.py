"""Exact line targets are proven against the frozen source, not against caller-supplied line labels (I12170 P1).

A line-faithful derivative (``normalized_snapshot``, ``faithful_markdown``) is a deterministic function of the
frozen text. Every adversarial derivative below keeps its node ids and ``line`` labels and is rehashed so its
manifest is self-consistent; only the correspondence between rendered text and frozen line can catch it. Each
is refused at the public validator, by ``open_review`` before anything is served, and by the protected
source-render / source-target routes if the asset is swapped after the review opened. Every value is synthetic.
"""
import copy
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from evidence_review import FileStore, open_review
from evidence_review.atomic_evidence import SourceRenderManifest
from evidence_review.contracts import Claim, digest
from evidence_review.source_rendering import (
    OriginalAsset,
    RenderedSourceAsset,
    prepare_render_asset,
    render_frozen_text,
    validate_render_asset,
)
from rendered_fixtures import html_bundle, html_sidecars, read
from synthetic import build, located, source
from test_form_binding_and_preparation import checked_revenue

ATOMIC = "atomic-source-check/v1"
NOT_CANONICAL = "does not correspond to the frozen source"


def rehashed(asset, mutate):
    """The auditor's forgery: change the derivative, then update only ``manifest.derivative_sha256``."""
    derivative = copy.deepcopy(asset.derivative)
    mutate(derivative)
    m = asset.manifest.model_dump(mode="json")
    m["derivative_sha256"] = digest(derivative)
    return RenderedSourceAsset(SourceRenderManifest.model_validate(m), derivative)


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children", []))


def node(derivative, predicate):
    return next(n for n in walk(derivative["nodes"]) if predicate(n))


def refused(b, asset, tmp_path, match=NOT_CANONICAL):
    with pytest.raises(ValueError, match=match):
        validate_render_asset(b, asset)
    with pytest.raises(ValueError, match=match):
        open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC)
    assert not (tmp_path / b.bundle_id).exists()


def get(h, path):
    with urlopen(Request(h.origin + path, headers={"Authorization": "Bearer " + h.token})) as r:
        return json.loads(r.read())


def status(h, path):
    try:
        get(h, path)
    except HTTPError as e:
        return e.code
    return 200


def table_bundle(cites, bundle_id="lines-table"):
    return build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=cites)],
                 sources=[source(read("table.frozen.md").decode())], bundle_id=bundle_id)


def set_text(derivative, old, new):
    hit = node(derivative, lambda n: n.get("text") == old)
    hit["text"] = new


# ---------------------------------------------------------------- the auditor's reproduction


@pytest.mark.parametrize("forged", ["Headcount was 10 people.", "Headcount was 99 people."])
def test_changed_line_text_with_same_id_line_and_rehash_is_refused(tmp_path, forged):
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    assert validate_render_asset(b, asset).manifest == asset.manifest
    exact = next(t for t in asset.manifest.targets if t.start_line == 1)
    assert exact.status == "exact" and exact.excerpt == "10"
    bad = rehashed(asset, lambda d: set_text(d, "Revenue was 10 units.", forged))
    target = node(bad.derivative, lambda n: n.get("line") == 1)
    assert target["id"] == exact.dom_targets[0] and target["text"] == forged
    refused(b, bad, tmp_path)


def test_faithful_markdown_line_text_change_is_refused_like_the_snapshot(tmp_path):
    b = checked_revenue()
    text = b.sources[0].text
    asset = prepare_render_asset(b, "filing", OriginalAsset(text.encode(), "text/markdown"))
    assert asset.manifest.render_mode == "faithful_markdown"
    refused(b, rehashed(asset, lambda d: set_text(d, "Revenue was 10 units.", "Headcount was 10 people.")), tmp_path)


# ---------------------------------------------------------------- tables: metric, period, cells


@pytest.mark.parametrize("old,new", [
    ("Operating margin", "Retail margin"),  # same number, wrong metric (row header)
    ("FY2026", "FY2025"),                   # same number, wrong period (column header)
    ("24.6%", "21.0%"),                     # the cited cell itself
    ("22.8%", "24.6%"),                     # a neighbouring cell of the cited row
    ("Metric", "Segment"),                  # the header row's own label
])
@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
def test_altered_table_headers_or_cells_are_refused(tmp_path, old, new, mode):
    b = table_bundle([located(7, "24.6%")])
    text = b.sources[0].text
    asset = (render_frozen_text(b, "filing") if mode == "normalized_snapshot"
             else prepare_render_asset(b, "filing", OriginalAsset(text.encode(), "text/markdown")))
    assert asset.manifest.render_mode == mode and asset.manifest.targets[0].status == "exact"

    def change(d):
        cells = [n for n in walk(d["nodes"]) if n.get("text") == old]
        cells[0]["text"] = new
    refused(b, rehashed(asset, change), tmp_path)


def test_swapped_table_rows_keeping_their_line_labels_are_refused(tmp_path):
    b = table_bundle([located(7, "24.6%"), located(9, "24.6%")])
    asset = render_frozen_text(b, "filing")

    def swap(d):
        seven = node(d, lambda n: n.get("line") == 7)
        nine = node(d, lambda n: n.get("line") == 9)
        seven["children"], nine["children"] = nine["children"], seven["children"]
    refused(b, rehashed(asset, swap), tmp_path)


# ---------------------------------------------------------------- line maps


def test_duplicate_missing_or_moved_line_mappings_are_refused(tmp_path):
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    one = node(asset.derivative, lambda n: n.get("line") == 1)["id"]
    two = node(asset.derivative, lambda n: n.get("line") == 2)["id"]

    def duplicate(d):
        node(d, lambda n: n["id"] == two)["line"] = 1

    def missing(d):
        del node(d, lambda n: n["id"] == one)["line"]

    def moved(d):  # Line labels exchanged: each node now claims the other's frozen line.
        node(d, lambda n: n["id"] == one)["line"] = 2
        node(d, lambda n: n["id"] == two)["line"] = 1

    def extra(d):  # A third node claiming line 1 in a fresh paragraph.
        d["nodes"].append({"id": "n999", "tag": "p", "children": [
            {"id": "n998", "tag": "line", "line": 1, "text": "Revenue was 10 units."}]})

    def dropped(d):  # The node holding line 2 removed: a smaller tree than the canonical rendering.
        d["nodes"].pop()

    for name, change in [("duplicate", duplicate), ("missing", missing), ("moved", moved), ("extra", extra),
                         ("dropped", dropped)]:
        refused(b, rehashed(asset, change), tmp_path / name)


def test_context_outside_the_cited_line_is_bound_too(tmp_path):
    """source-render serves the whole derivative, so an uncited line changed around an exact target is refused."""
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    bad = rehashed(asset, lambda d: set_text(d, "Headcount was 99 people.", "Headcount was 12 people."))
    refused(b, bad, tmp_path)


def test_rehashed_derivative_with_no_exact_target_is_still_refused(tmp_path):
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    m = asset.manifest.model_dump(mode="json")
    for t in m["targets"]:
        t.update(status="unavailable", dom_targets=[], reason="caller withholds the location")
    m["mapping_sha256"] = digest(m["targets"])
    weaker = RenderedSourceAsset(SourceRenderManifest.model_validate(m), asset.derivative)
    assert validate_render_asset(b, weaker).manifest == weaker.manifest  # Honest downgrade stays accepted.
    refused(b, rehashed(weaker, lambda d: set_text(d, "Revenue was 10 units.", "Headcount was 10 people.")),
            tmp_path)


# ---------------------------------------------------------------- valid renderings keep exact mappings


@pytest.mark.parametrize("name", ["escaped.md", "table.frozen.md", "hostile.frozen.md"])
@pytest.mark.parametrize("mode", ["normalized_snapshot", "faithful_markdown"])
def test_package_renderings_of_escapes_tables_headings_and_multiline_stay_exact(tmp_path, name, mode):
    text = read(name).decode()
    lines = text.splitlines()
    def last(line):
        words = [w for w in line.split() if w not in ("|", "\\|")]
        return words[-1] if words and not set(line) <= set("|- :") else None
    cites = [located(i + 1, last(line)) for i, line in enumerate(lines) if last(line)]
    multi = next(i for i in range(len(lines) - 1) if last(lines[i]) and last(lines[i + 1]))
    cites.append(type(cites[0])(source_id="filing", start_line=multi + 1, end_line=multi + 2,
                                excerpt=last(lines[multi]), status="located"))
    b = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=cites)],
              sources=[source(text)], bundle_id="valid-" + name.replace(".", "-") + "-" + mode.replace("_", "-"))
    asset = (render_frozen_text(b, "filing") if mode == "normalized_snapshot"
             else prepare_render_asset(b, "filing", OriginalAsset(text.encode(), "text/markdown")))
    assert asset.manifest.render_mode == mode
    assert all(t.status == "exact" and t.dom_targets for t in asset.manifest.targets)
    assert any(t.end_line > t.start_line for t in asset.manifest.targets)
    assert validate_render_asset(b, asset).manifest == asset.manifest
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        body = get(h, "/api/source-render/filing")
        assert body["derivative"] == asset.derivative
        for t in asset.manifest.targets:
            served = get(h, "/api/source-target/" + t.target_id)["target"]
            assert (served["status"], served["dom_targets"]) == ("exact", t.dom_targets)


def test_escaped_pipe_row_keeps_its_exact_mapping_and_an_unescaped_forgery_is_refused(tmp_path):
    text = read("escaped.md").decode()
    b = build([("summary", "Margin was 24.6%.", ["c"])],
              [Claim(claim_id="c", text="x", citations=[located(4, "24.6% \\| reported")])],
              sources=[source(text)], bundle_id="escaped-row")
    asset = render_frozen_text(b, "filing")
    (t,) = asset.manifest.targets
    assert t.status == "exact"
    # Dropping the escape changes what the reviewer reads (a new cell boundary); _key() would ignore it.
    bad = rehashed(asset, lambda d: node(d, lambda n: n.get("line") == 4).update(
        text=node(d, lambda n: n.get("line") == 4)["text"].replace("\\|", "|")))
    refused(b, bad, tmp_path)


# ---------------------------------------------------------------- unsupported mappings stay limited


def test_ambiguous_and_page_only_mappings_cannot_be_forged_exact(tmp_path):
    html = b"<p>Margin was 24.6% this year.</p><p>Margin was 24.6% this year.</p>"
    b = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=[located(1, "24.6%")])],
              sources=[source("Margin was 24.6% this year.\nMargin was 24.6% this year.\n")], bundle_id="amb")
    asset = prepare_render_asset(b, "filing", OriginalAsset(html, "text/html"))
    (t,) = asset.manifest.targets
    assert t.status == "ambiguous"

    def as_exact(a, page=None, bbox=None):
        m = a.manifest.model_dump(mode="json")
        for x in m["targets"]:
            first = x["candidates"][0] if x["candidates"] else [a.derivative["nodes"][0]["id"]]
            x.update(status="exact", dom_targets=first, candidates=[], reason="", page=page, bbox=bbox)
        m["mapping_sha256"] = digest(m["targets"])
        return RenderedSourceAsset(SourceRenderManifest.model_validate(m), a.derivative)
    refused(b, as_exact(asset), tmp_path / "amb", "does not prove")

    sb = build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=[located(1, "24.6%")])],
               sources=[source(read("scanned.frozen.txt").decode())], bundle_id="scan")
    scanned = prepare_render_asset(sb, "filing", OriginalAsset(read("scanned.pdf"), "application/pdf",
                                                               page_map={1: 1}))
    assert scanned.manifest.targets[0].status == "page_only"
    refused(sb, as_exact(scanned, page=1), tmp_path / "page", "does not prove")


# ---------------------------------------------------------------- protected routes re-prove on every request


def test_render_routes_refuse_a_rehashed_forgery_swapped_in_after_open(tmp_path):
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    exact = next(t for t in asset.manifest.targets if t.start_line == 1)
    with open_review(b, FileStore(tmp_path), launch=False, rendered_sources=[asset], presentation_mode=ATOMIC) as h:
        assert get(h, "/api/source-target/" + exact.target_id)["target"]["status"] == "exact"
        assert "Revenue was 10 units." in json.dumps(get(h, "/api/source-render/filing")["derivative"])
        # Forge in place: new text, then the manifest's derivative hash, so ``verify()`` alone would pass.
        node(asset.derivative, lambda n: n.get("line") == 1)["text"] = "Headcount was 10 people."
        asset.manifest.derivative_sha256 = digest(asset.derivative)
        asset.verify()
        assert status(h, "/api/source-render/filing") == 500
        assert status(h, "/api/source-target/" + exact.target_id) == 500


def test_html_render_routes_still_serve_a_valid_asset(tmp_path):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode=ATOMIC) as h:
        exact = next(t for t in asset.manifest.targets if t.status == "exact")
        assert get(h, "/api/source-target/" + exact.target_id)["target"]["dom_targets"] == exact.dom_targets
        assert get(h, "/api/source-render/filing")["derivative"] == asset.derivative


def test_repeated_node_id_in_a_line_derivative_is_refused_before_the_line_proof(tmp_path):
    b = checked_revenue()
    asset = render_frozen_text(b, "filing")
    two = node(asset.derivative, lambda n: n.get("line") == 2)["id"]
    bad = rehashed(asset, lambda d: node(d, lambda n: n.get("line") == 1).update(id=two))
    refused(b, bad, tmp_path, "repeats a node id")
