"""A rendered target's ambiguous candidates are distinct identities (sibling of I12177).

``RenderedSourceTarget.candidates`` used to be checked by length only, so one rendered
location listed twice satisfied "at least two candidates" and the viewer showed
"Candidate 1 of 2" and "Candidate 2 of 2" highlighting the same node. A candidate's
identity is the set of rendered nodes it highlights; repeats are refused by the model,
the published schema declares ``uniqueItems``, the builders degrade a repeat-only
location to unavailable, and the protected render route never serves a repeat.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_review.atomic_evidence import RenderedSourceTarget, SourceRenderManifest
from evidence_review.contracts import digest
from evidence_review.source_rendering import (REPEATED_CANDIDATE, OriginalAsset, RenderedSourceAsset,
                                              distinct_candidates, prepare_render_asset, validate_render_asset)
from synthetic import located
from test_source_rendering import bundle_for

ROOT = Path(__file__).parents[1]
TID = "tgt:" + "0" * 24


def _target(candidates):
    return dict(target_id=TID, source_id="filing", frozen_sha256="f" * 64, start_line=1, end_line=1,
                excerpt="24.6%", candidates=candidates, status="ambiguous", reason="Excerpt repeats")


@pytest.mark.parametrize("candidates", [
    pytest.param([["n1"], ["n1"]], id="duplicate-only"),
    pytest.param([["n1"], ["n2"], ["n1"]], id="mixed-duplicate"),
    pytest.param([["n1", "n2"], ["n2", "n1"]], id="same-nodes-reordered"),
])
def test_duplicate_render_candidates_are_refused_by_the_model_and_the_wire_form(candidates):
    with pytest.raises(ValidationError, match="duplicate render candidate"):
        RenderedSourceTarget.model_validate(_target(candidates))
    with pytest.raises(ValidationError, match="duplicate render candidate"):
        RenderedSourceTarget.model_validate_json(json.dumps(_target(candidates)))


def test_two_distinct_render_candidates_remain_valid():
    t = RenderedSourceTarget.model_validate(_target([["n2"], ["n1", "n3"]]))
    assert t.status == "ambiguous" and t.candidates == [["n2"], ["n1", "n3"]]


def test_published_schema_declares_render_candidate_uniqueness():
    expected = SourceRenderManifest.model_json_schema()
    prop = expected["$defs"]["RenderedSourceTarget"]["properties"]["candidates"]
    assert prop["uniqueItems"] is True and prop["type"] == "array"
    for folder in (ROOT / "schemas", ROOT / "src" / "evidence_review" / "schemas"):
        assert json.loads((folder / "source-render-manifest-v1.json").read_text()) == expected


def test_distinct_candidates_keeps_first_seen_order_by_node_set():
    assert distinct_candidates([["b"], ["a", "c"], ["b"], ["c", "a"], ["d"]]) == [["b"], ["a", "c"], ["d"]]


def _pdf(*runs):
    """A one-page synthetic PDF whose text layer is exactly ``runs``, one run per line."""
    widths = " ".join(["500"] * 95)
    body = "BT /F1 12 Tf 72 700 Td 14 TL " + " ".join(f"({r}) Tj T*" for r in runs) + " ET"
    objs = [
        f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 /LastChar 126 /Widths [{widths}] >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Page /Parent 4 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 1 0 R >> >> "
        "/Contents 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Catalog /Pages 4 0 R >>",
    ]
    return ("%PDF-1.4\n" + "".join(f"{i} 0 obj\n{o}\nendobj\n" for i, o in enumerate(objs, 1))
            + "trailer\n<< /Root 5 0 R >>\n%%EOF\n").encode()


def _pdf_target(*runs):
    b = bundle_for("24.6%\n", [located(1, "24.6%")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(_pdf(*runs), "application/pdf"))
    validate_render_asset(b, asset)
    (t,) = asset.manifest.targets
    return t


def test_pdf_repeat_within_one_run_degrades_to_unavailable_never_two_candidates():
    t = _pdf_target("24.6% then 24.6%")
    assert t.status == "unavailable" and t.candidates == [] and t.dom_targets == []
    assert t.reason == REPEATED_CANDIDATE


def test_pdf_mixed_repeat_keeps_only_distinct_locations():
    t = _pdf_target("24.6% then 24.6%", "24.6%")
    assert t.status == "ambiguous" and len(t.candidates) == 2
    assert len({frozenset(c) for c in t.candidates}) == 2 and "2 places" in t.reason


def test_pdf_two_distinct_runs_stay_ambiguous():
    t = _pdf_target("24.6%", "24.6%")
    assert t.status == "ambiguous" and len(t.candidates) == 2 and t.candidates[0] != t.candidates[1]


def test_render_route_refuses_a_manifest_mutated_to_repeat_a_candidate():
    b = bundle_for("24.6%\n", [located(1, "24.6%")])
    asset = prepare_render_asset(b, "filing", OriginalAsset(_pdf("24.6%", "24.6%"), "application/pdf"))
    (t,) = asset.manifest.targets
    forged = RenderedSourceTarget.model_construct(**{**t.__dict__, "candidates": [t.candidates[0]] * 2})
    targets = [forged]
    manifest = SourceRenderManifest.model_construct(**{
        **asset.manifest.__dict__, "targets": targets,
        "mapping_sha256": digest([x.model_dump(mode="json", warnings=False) for x in targets])})
    with pytest.raises(ValidationError, match="duplicate render candidate"):
        validate_render_asset(b, RenderedSourceAsset(manifest, asset.derivative))
