"""An ambiguous numeric span with fewer than two located candidates degrades to one unavailable atom row.

Real-corpus shape (synthetic values): a producer marks a prose number ``ambiguous`` because it restates
numbers in several claims with different evidence, and lists those claims, but attaches no candidate
citation to the span itself (or a reference-level quantity marked ambiguous with no span citation). The
bundle contract accepts that span; the default inventory used to refuse the WHOLE bundle for it.
"""
import pytest

from evidence_review.atomic_evidence import (
    AtomEvidenceManifest,
    atom_view,
    build_atom_manifest,
    citation_target_id,
    validate_atom_evidence,
)
from evidence_review.contracts import Claim, validate_bundle
from synthetic import build, located, margin_claim, source, unavailable

TEXT = "Operating margin reached 24.6% and revenue was 1,150 in the year."
RESTATED = "restates numbers in several claims with different evidence"


def _bundle(ambiguous_citations=(), claim_ids=("margin", "revenue"), sources=None):
    revenue = Claim(claim_id="revenue", text="Revenue was 1,150.", citations=[located(7, "1,150")])
    other = Claim(claim_id="other", text="Revenue was 1,150 on another basis.", citations=[located(7, "1,200")])
    b = build([("summary", TEXT, ["margin"])], [margin_claim(), revenue, other], sources=sources)
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        if s["text"] == "24.6%":
            s.update(state="cited", claim_ids=["margin"], citations=[located(6, "24.6%").model_dump(mode="json")])
        elif s["text"] == "1,150":
            s.update(state="ambiguous", claim_ids=list(claim_ids), reason=RESTATED,
                     citations=[c.model_dump(mode="json") for c in ambiguous_citations])
    return validate_bundle(data)


def _row(m, text):
    return next(i for i in m.items if i.text == text)


@pytest.mark.parametrize("citations", [
    pytest.param((), id="no-span-citation"),            # the 155 prose restatements and 186 reference quantities
    pytest.param((located(7, "1,150"),), id="one-located"),
    pytest.param((located(7, "1,150"), unavailable()), id="one-located-one-unlocated"),
])
def test_ambiguous_span_with_fewer_than_two_targets_is_unavailable_and_bundle_is_whole(citations):
    b = _bundle(citations)
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    AtomEvidenceManifest.model_validate(m.model_dump(mode="json"))
    row = _row(m, "1,150")
    # Never shown as exact: no citation target, no single candidate promoted.
    assert row.evidence_state == "unavailable"
    assert row.citation_target_ids == [] and row.candidate_target_ids == []
    assert RESTATED in row.reason and "ambiguous" in row.reason.lower()
    assert row.claim_ids == ["margin", "revenue"]
    # Every other atom of the bundle is still built and unaffected.
    assert _row(m, "24.6%").evidence_state == "located"
    assert {i.text for i in m.items} == {"24.6%", "1,150"}
    assert atom_view(b, m)


def test_ambiguous_reason_defaults_when_producer_gave_none():
    b = _bundle()
    data = b.model_dump(mode="json")
    next(s for s in data["spans"] if s["text"] == "1,150")["reason"] = ""
    b = validate_bundle(data)
    row = _row(build_atom_manifest(b), "1,150")
    assert row.evidence_state == "unavailable" and "ambiguous" in row.reason.lower()


def test_assigned_ambiguous_span_without_candidates_builds_one_unavailable_row():
    b = _bundle()
    span = next(s.span_id for s in b.spans if s.text == "1,150")
    m = build_atom_manifest(b, coverage="assigned", assigned_span_ids=[span])
    validate_atom_evidence(b, m)
    assert [i.evidence_state for i in m.items] == ["unavailable"]


def test_ambiguous_span_with_two_located_candidates_stays_ambiguous():
    text = source().text + "| Revenue restated | 1,150 |\n"
    b = _bundle((located(7, "1,150"), located(len(text.splitlines()), "1,150")), sources=[source(text)])
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    row = _row(m, "1,150")
    assert row.evidence_state == "ambiguous" and len(set(row.candidate_target_ids)) == 2
    assert row.citation_target_ids == []


def test_caller_manifest_cannot_show_an_ambiguous_span_as_located():
    b = _bundle((located(7, "1,150"),))
    data = build_atom_manifest(b).model_dump(mode="json")
    item = next(i for i in data["items"] if i["text"] == "1,150")
    # The span's own single located candidate, promoted to located by hand.
    item.update(evidence_state="located", reason="", citation_target_ids=[citation_target_id(b, located(7, "1,150"))])
    with pytest.raises(ValueError, match="ambiguous"):
        validate_atom_evidence(b, data)
