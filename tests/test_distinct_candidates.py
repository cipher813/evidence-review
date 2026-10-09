"""An ambiguous atom's candidates are distinct identities at every ingress (I12177).

Synthetic repro: one located citation, the atom hand-edited to ``ambiguous`` with
``candidate_target_ids=[tid, tid]``. One target listed twice is one candidate, so
the model, the JSON/schema path, the builder and the browser view all refuse to
present it as two alternatives.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_review.atomic_evidence import (AtomEvidenceItem, AtomEvidenceManifest, atom_view,
                                             build_atom_manifest, citation_target_id, validate_atom_evidence)
from synthetic import located, source
from test_ambiguous_atom_degrade import RESTATED, _bundle, _row
from test_atomic_evidence import FACT, TWIN, bundle, fact

ROOT = Path(__file__).parents[1]


def _ambiguous_payload(candidates):
    """A caller manifest whose ambiguous row carries exactly ``candidates``."""
    text = source().text + "| Revenue restated | 1,150 |\n"
    two = (located(7, "1,150"), located(len(text.splitlines()), "1,150"))
    b = _bundle(two, sources=[source(text)])
    t1, t2 = (citation_target_id(b, c) for c in two)
    data = build_atom_manifest(b).model_dump(mode="json")
    row = next(i for i in data["items"] if i["text"] == "1,150")
    assert row["evidence_state"] == "ambiguous" and sorted(row["candidate_target_ids"]) == sorted([t1, t2])
    row["candidate_target_ids"] = [{"t1": t1, "t2": t2}[k] for k in candidates]
    return b, data


@pytest.mark.parametrize("candidates", [
    pytest.param(["t1", "t1"], id="duplicate-only"),
    pytest.param(["t1", "t2", "t1"], id="mixed-duplicate"),
    pytest.param(["t2", "t1", "t2", "t2"], id="mixed-repeated"),
])
def test_duplicate_candidate_identities_are_refused_at_every_ingress(candidates):
    b, data = _ambiguous_payload(candidates)
    with pytest.raises(ValueError, match="duplicate candidate target"):
        validate_atom_evidence(b, data)
    # The JSON wire form is refused by the model itself, before any bundle check.
    with pytest.raises(ValidationError, match="duplicate candidate target"):
        AtomEvidenceManifest.model_validate_json(json.dumps(data))
    row = next(i for i in data["items"] if i["text"] == "1,150")
    with pytest.raises(ValidationError, match="duplicate candidate target"):
        AtomEvidenceItem.model_validate(row)


def test_exactly_two_distinct_candidates_remain_valid():
    b, data = _ambiguous_payload(["t2", "t1"])
    m = validate_atom_evidence(b, data)
    row = _row(m, "1,150")
    assert row.evidence_state == "ambiguous" and len(set(row.candidate_target_ids)) == 2
    view = next(r for r in atom_view(b, m)["rows"] if r["text"] == "1,150")
    assert [c["target_id"] for c in view["candidates"]] == row.candidate_target_ids


def test_published_schema_declares_candidate_uniqueness():
    expected = AtomEvidenceManifest.model_json_schema()
    item = expected["$defs"]["AtomEvidenceItem"]["properties"]["candidate_target_ids"]
    assert item["uniqueItems"] is True and item["type"] == "array"
    for folder in (ROOT / "schemas", ROOT / "src" / "evidence_review" / "schemas"):
        assert json.loads((folder / "atom-evidence-v1.json").read_text()) == expected


def test_builder_degrades_a_repeated_span_candidate_to_unavailable_without_promotion():
    b = _bundle((located(7, "1,150"), located(7, "1,150")))
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    row = _row(m, "1,150")
    assert row.evidence_state == "unavailable"
    assert row.citation_target_ids == [] and row.candidate_target_ids == []
    assert RESTATED in row.reason


@pytest.mark.parametrize("declared", [{}, {"evidence_state": "ambiguous", "reason": "Restated twice"}])
def test_builder_degrades_a_repeated_fact_citation_to_unavailable_without_promotion(declared):
    b = bundle()
    same = located(10, "Management expects demand to soften next year.")
    m = build_atom_manifest(b, [fact(b, citations=[same, same], **declared)])
    validate_atom_evidence(b, m)
    row = next(i for i in m.items if i.kind == "fact")
    assert row.text == FACT and row.evidence_state == "unavailable"
    assert row.citation_target_ids == [] and row.candidate_target_ids == []
    assert "ambiguous" in row.reason.lower() and declared.get("reason", "") in row.reason


def test_builder_fact_with_two_distinct_and_one_repeated_citation_keeps_two_candidates():
    text = source().text + TWIN
    b = bundle(sources=[source(text)])
    a, z = located(6, "24.6%"), located(len(text.splitlines()), "24.6%")
    m = build_atom_manifest(b, [fact(b, citations=[a, z, a], reason="Two rows hold this wording")])
    validate_atom_evidence(b, m)
    row = next(i for i in m.items if i.kind == "fact")
    assert row.evidence_state == "ambiguous"
    assert row.candidate_target_ids == [citation_target_id(b, a), citation_target_id(b, z)]


def test_view_refuses_a_manifest_mutated_past_validation():
    b, data = _ambiguous_payload(["t1", "t2"])
    m = validate_atom_evidence(b, data)
    row = _row(m, "1,150")
    row.candidate_target_ids = [row.candidate_target_ids[0]] * 2  # no validate_assignment on the model
    with pytest.raises(ValueError, match="duplicate candidate target"):
        atom_view(b, m)
