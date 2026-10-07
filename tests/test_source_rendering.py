"""Offline rendered sources: fidelity labels, exact/ambiguous/page-only targets, bounds and protected routes."""
import hashlib
import json
import zlib
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from evidence_review import FileStore, Hooks, capabilities, open_review
from evidence_review.contracts import Claim, digest
from evidence_review.server import BlindingViolation, UnsupportedContract
from evidence_review.source_rendering import (
    OriginalAsset,
    RenderLimitExceeded,
    RenderLimits,
    check_derivative,
    prepare_render_asset,
    render_frozen_text,
    resolve_render_target,
)
from rendered_fixtures import FIXTURES, html_bundle, html_sidecars, read
from synthetic import build, located, source


def bundle_for(text, cites, bundle_id="render"):
    return build([("summary", "Margin was 24.6%.", ["c"])], [Claim(claim_id="c", text="x", citations=cites)],
                 sources=[source(text)], bundle_id=bundle_id)


def targets(asset):
    return {(t.start_line, t.excerpt): t for t in asset.manifest.targets}


def all_text(nodes):
    return json.dumps(nodes)


def test_markdown_original_identical_to_frozen_is_faithful_and_unproven_table_stays_numbered():
    raw = read("escaped.md")
    b = bundle_for(raw.decode(), [located(4, "24.6%"), located(9, "24.6%")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "text/markdown"))
    m = asset.manifest
    assert (m.render_mode, m.lineage) == ("faithful_markdown", "raw_matches_frozen")
    assert m.raw_sha256 == hashlib.sha256(raw).hexdigest() == b.sources[0].sha256
    assert any("not proven" in t for t in m.transforms)
    assert all(t.status == "exact" and t.dom_targets for t in m.targets)
    # Escaped pipes and the short row are not reconstructed as a table.
    assert '"tag": "table"' not in all_text(asset.derivative)
    assert read("escaped.md") == raw  # Original bytes untouched.


def test_frozen_text_rendering_is_labelled_normalized_and_tables_keep_headers():
    frozen = read("table.frozen.md").decode()
    b = bundle_for(frozen, [located(7, "24.6%"), located(9, "24.6%")])
    asset = render_frozen_text(b, "filing")
    m = asset.manifest
    assert (m.render_mode, m.lineage, m.raw_sha256) == ("normalized_snapshot", "raw_unavailable", None)
    assert "not claimed" in m.fidelity_note
    tree = all_text(asset.derivative)
    assert '"tag": "table"' in tree and "FY2026" in tree and "(1) Revenue restated" in tree
    first, second = targets(asset)[(7, "24.6%")], targets(asset)[(9, "24.6%")]
    assert first.dom_targets != second.dom_targets and len(first.dom_targets) == 1


def test_html_table_targets_exact_cells_and_disambiguates_repeated_values_by_row():
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    m = asset.manifest
    assert (m.render_mode, m.lineage) == ("faithful_html", "raw_derived_text")
    by_line = {t.start_line: t for t in m.targets}
    assert by_line[7].status == "exact" and by_line[7].dom_targets
    assert by_line[12].status == "exact"
    tree = all_text(asset.derivative)
    for kept in ("Table 1. Segment results by fiscal year", "(1) Revenue restated", "FY2025", "Operating margin"):
        assert kept in tree
    for gone in ("cdn.example.invalid", "color: red", "<style", "stylesheet"):
        assert gone not in tree
    check_derivative(asset.derivative)


def test_html_duplicates_without_row_identity_stay_ambiguous():
    html = b"<p>Margin was 24.6% this year.</p><p>Margin was 24.6% this year.</p>"
    b = bundle_for("Margin was 24.6% this year.\nMargin was 24.6% this year.\n", [located(1, "24.6%")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(html, "text/html"))
    (t,) = asset.manifest.targets
    assert t.status == "ambiguous" and len(t.candidates) == 2 and not t.dom_targets and t.reason


def test_hostile_html_is_inert_offline_and_every_removal_is_declared():
    raw = read("hostile.html")
    b = bundle_for(read("hostile.frozen.md").decode(), [located(2, "Management expects")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "text/html"))
    tree = all_text(asset.derivative)
    for leaked in ("http", "javascript", "alert", "fetch(", "cookie", "onload", "onclick", "onerror", "steal",
                   "refresh", "token"):
        assert leaked not in tree.lower(), leaked
    tags = set()

    def walk(nodes):
        for n in nodes:
            tags.add(n["tag"])
            assert set(n.get("attrs", {})) <= {"colspan", "rowspan", "scope"}
            walk(n.get("children", []))
    walk(asset.derivative["nodes"])
    assert not tags & {"script", "style", "iframe", "img", "form", "input", "svg", "object", "a", "link", "meta"}
    transforms = " ".join(asset.manifest.transforms)
    for declared in ("removed <iframe>", "removed <img>", "removed <svg>", "removed <object>",
                     "removed event-handler attribute", "made link inert", "removed href attribute"):
        assert declared in transforms
    assert "inert link text" in tree and "Capital spending was 310" in tree
    assert raw == read("hostile.html")


def test_pdf_text_layer_targets_page_and_box_and_keeps_duplicates_ambiguous():
    raw = read("two-page.pdf")
    frozen = read("two-page.frozen.txt").decode()
    b = bundle_for(frozen, [located(3, "24.6%"), located(6, "24.6%"), located(8, "Management expects")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "application/pdf"))
    m = asset.manifest
    assert m.render_mode == "pdf_text_layer" and asset.derivative["pages"] == 2
    t3, t6, t8 = (targets(asset)[k] for k in [(3, "24.6%"), (6, "24.6%"), (8, "Management expects")])
    assert (t3.status, t3.page) == ("exact", 1) and (t6.status, t6.page) == ("exact", 2)
    assert t3.bbox and t3.bbox[0] == 72.0 and t3.bbox[2] > t3.bbox[0]
    assert t8.page == 2
    # A cited line that is just the number cannot be told apart across pages.
    loose = bundle_for("24.6%\n", [located(1, "24.6%")])
    (amb,) = prepare_render_asset(loose, "filing", OriginalAsset(raw, "application/pdf")).manifest.targets
    assert amb.status == "ambiguous" and len(amb.candidates) == 2


def test_scanned_pdf_is_page_only_with_caller_page_map_and_unavailable_without():
    raw = read("scanned.pdf")
    b = bundle_for(read("scanned.frozen.txt").decode(), [located(1, "24.6%")])
    (t,) = prepare_render_asset(b, "filing", OriginalAsset(raw, "application/pdf", page_map={1: 1})).manifest.targets
    assert (t.status, t.page) == ("page_only", 1) and "text layer" in t.reason
    asset = prepare_render_asset(b, "filing", OriginalAsset(raw, "application/pdf"))
    assert asset.manifest.targets[0].status == "unavailable"
    assert "no extractable text layer" in all_text(asset.derivative)


def test_lineage_labels_hash_declaration_and_unsupported_formats():
    b = html_bundle()
    raw = read("table.html")
    differs = prepare_render_asset(b, "filing", OriginalAsset(raw, "text/html", frozen_derived=False))
    assert differs.manifest.lineage == "raw_differs_from_frozen"
    with pytest.raises(ValueError, match="declared SHA-256"):
        prepare_render_asset(b, "filing", OriginalAsset(raw, "text/html", declared_sha256="0" * 64))
    png = prepare_render_asset(b, "filing", OriginalAsset(b"\x89PNG\r\n", "image/png"))
    assert png.manifest.render_mode == "normalized_snapshot" and "not renderable" in " ".join(png.manifest.transforms)
    other = prepare_render_asset(b, "filing", OriginalAsset(b"different", "text/markdown"))
    assert other.manifest.render_mode == "normalized_snapshot" and other.manifest.lineage == "raw_derived_text"


def test_resource_bounds_fail_visibly_instead_of_hanging():
    b = html_bundle()
    with pytest.raises(RenderLimitExceeded):
        prepare_render_asset(b, "filing", OriginalAsset(read("table.html"), "text/html"), limits=RenderLimits(max_bytes=10))
    with pytest.raises(RenderLimitExceeded):
        prepare_render_asset(b, "filing", OriginalAsset(read("table.html"), "text/html"), limits=RenderLimits(max_work=20))
    with pytest.raises(RenderLimitExceeded):
        prepare_render_asset(b, "filing", OriginalAsset(read("table.html"), "text/html"), limits=RenderLimits(max_nodes=5))
    pdf = bundle_for(read("two-page.frozen.txt").decode(), [located(3, "24.6%")])
    with pytest.raises(RenderLimitExceeded):
        prepare_render_asset(pdf, "filing", OriginalAsset(read("two-page.pdf"), "application/pdf"),
                             limits=RenderLimits(max_pages=1))
    bomb = zlib.compress(b"0" * 3_000_000)
    data = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(bomb)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + bomb + b"\nendstream\nendobj\n%%EOF\n")
    with pytest.raises(RenderLimitExceeded):
        prepare_render_asset(pdf, "filing", OriginalAsset(data, "application/pdf"),
                             limits=RenderLimits(max_bytes=1_000_000))


def test_derivative_and_mapping_are_hash_bound_and_deterministic():
    b = html_bundle()
    _, asset = html_sidecars(b)
    _, again = html_sidecars(b)
    assert asset.manifest == again.manifest and digest(asset.derivative) == asset.manifest.derivative_sha256
    target = asset.manifest.targets[0]
    assert resolve_render_target(asset.manifest, target.target_id) == target
    with pytest.raises(ValueError, match="not in this rendered source"):
        resolve_render_target(asset.manifest, "tgt:" + "0" * 24)
    asset.derivative["nodes"].append({"id": "x", "tag": "p", "text": "injected"})
    with pytest.raises(ValueError, match="changed after preparation"):
        asset.verify()
    with pytest.raises(ValueError, match="allowlist"):
        check_derivative({"schema_version": "rendered-source/v1", "nodes": [{"id": "s", "tag": "script"}]})
    with pytest.raises(ValueError, match="unsafe attribute"):
        check_derivative({"schema_version": "rendered-source/v1",
                          "nodes": [{"id": "s", "tag": "p", "attrs": {"onclick": "x"}}]})


def get(h, path, token=None, host=None):
    headers = {"Authorization": "Bearer " + (token or h.token)}
    if host:
        headers["Host"] = host
    with urlopen(Request(h.origin + path, headers=headers)) as r:
        return json.loads(r.read())


def test_render_routes_are_authenticated_allowlisted_and_reverify_bytes(tmp_path):
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset],
                     presentation_mode="atomic-source-check/v1", require_contracts=["atom-evidence/v1"]) as h:
        body = get(h, "/api/source-render/filing")
        assert body["manifest"]["derivative_sha256"] == digest(body["derivative"])
        tid = body["manifest"]["targets"][0]["target_id"]
        assert get(h, "/api/source-target/" + tid)["target"]["target_id"] == tid
        evidence = get(h, "/api/evidence")
        assert evidence["presentation"]["mode"] == "atomic-source-check/v1"
        assert evidence["rendered_sources"]["filing"]["render_mode"] == "faithful_html"
        assert len(evidence["atoms"]["rows"]) == 4 and evidence["capabilities"] == capabilities()
        for headers in ({"token": "wrong"}, {"host": "localhost:1"}):
            with pytest.raises(HTTPError) as e:
                get(h, "/api/source-render/filing", **headers)
            assert e.value.code == 403
        for path in ("/api/source-render/other", "/api/source-render/..%2F..%2Fetc%2Fpasswd",
                     "/api/source-render/../bundle", "/api/source-target/tgt:" + "0" * 24,
                     "/api/source-target/../../state"):
            with pytest.raises(HTTPError) as e:
                get(h, path)
            assert e.value.code in (403, 404), path
        with urlopen(Request(h.origin + "/api/source-render/filing",
                             headers={"Authorization": "Bearer " + h.token})) as r:
            assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        asset.derivative["nodes"].append({"id": "x", "tag": "p", "text": "changed"})
        with pytest.raises(HTTPError) as e:
            get(h, "/api/source-render/filing")
        assert e.value.code == 500


def test_render_response_bound(tmp_path, monkeypatch):
    import evidence_review.server as server
    b = html_bundle()
    atoms, asset = html_sidecars(b)
    monkeypatch.setattr(server, "MAX_RENDER_RESPONSE", 100)
    with open_review(b, FileStore(tmp_path), launch=False, atom_evidence=atoms, rendered_sources=[asset]) as h:
        with pytest.raises(HTTPError) as e:
            get(h, "/api/source-render/filing")
        assert e.value.code == 413


def test_sidecars_refuse_wrong_bundle_blinded_text_and_unknown_contracts(tmp_path):
    b = html_bundle()
    other = html_bundle(task_kind="finding")
    _, foreign = html_sidecars(other)
    with pytest.raises(ValueError, match="different bundle"):
        open_review(b, FileStore(tmp_path / "a"), launch=False, rendered_sources=[foreign])
    _, asset = html_sidecars(b)
    with pytest.raises(BlindingViolation):
        open_review(html_bundle(task_kind="independent"), FileStore(tmp_path / "b"), launch=False,
                    presentation_mode="atomic-source-check/v1", blind_markers=["Segment results"])
    with pytest.raises(UnsupportedContract):
        open_review(b, FileStore(tmp_path / "c"), launch=False, require_contracts=["atom-evidence/v2"])
    with pytest.raises(UnsupportedContract):
        open_review(b, FileStore(tmp_path / "d"), launch=False, presentation_mode="atomic-source-check/v2")
    with pytest.raises(TypeError):
        open_review(b, FileStore(tmp_path / "e"), launch=False, rendered_sources=[{"manifest": {}}])
    assert not (tmp_path / "a" / b.bundle_id).exists()


def test_next_task_refreshes_rendered_sidecars(tmp_path):
    from urllib.request import urlopen as raw
    first = html_bundle()
    second = html_bundle(task_kind="finding")
    seen = []

    def renders(bundle):
        seen.append(bundle.bundle_id)
        return [html_sidecars(bundle)[1]]

    queue = [second]
    hooks = Hooks(on_submission=lambda s: {"status": "succeeded", "identifier": "r"}, next_bundle=lambda: queue.pop() if queue else None)
    with open_review(first, FileStore(tmp_path), hooks=hooks, launch=False, rendered_sources=renders,
                     presentation_mode="atomic-source-check/v1") as h:
        answers = {"judgments": {"verdict:margin": {"value": "cannot_verify", "note": "Synthetic"},
                                 "report_complete": {"value": True}}, "defects": []}
        def post(path, body):
            req = Request(h.origin + path, data=json.dumps(body).encode(), method="POST",
                          headers={"Authorization": "Bearer " + h.token, "Content-Type": "application/json",
                                   "Origin": h.origin})
            with raw(req) as r:
                return json.loads(r.read())
        ident = {"bundle_id": first.bundle_id, "bundle_hash": first.bundle_hash}
        post("/api/submit", {**ident, "revision": 0, "key": "k", "answers": answers, "assessor": "Ada"})
        post("/api/next", ident)
        assert get(h, "/api/source-render/filing")["manifest"]["bundle_hash"] == second.bundle_hash
    assert seen == [first.bundle_id, second.bundle_id]
