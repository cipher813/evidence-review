"""A numeric span whose own located citations repeat keeps each distinct candidate once (I12181).

Synthetic repro: a span cites [A, B, A]. The frozen bundle contract accepts the repeat; the default
inventory used to refuse the WHOLE manifest with "duplicate candidate target" instead of presenting the
two distinct alternatives. Caller-supplied manifests stay strict (test_distinct_candidates).
"""
import pytest

from evidence_review.atomic_evidence import (AtomEvidenceManifest, build_atom_manifest, citation_target_id,
                                             validate_atom_evidence)
from synthetic import located, source
from test_ambiguous_atom_degrade import RESTATED, _bundle, _row

TEXT = source().text + "| Revenue restated | 1,150 |\n"
A, B = located(7, "1,150"), located(len(TEXT.splitlines()), "1,150")


def _cited(citations):
    """The same shape with the span declared ``cited`` rather than ``ambiguous``."""
    b = _bundle(citations, sources=[source(TEXT)])
    data = b.model_dump(mode="json")
    span = next(s for s in data["spans"] if s["text"] == "1,150")
    span.update(state="cited", reason=RESTATED)
    return type(b).model_validate(data)


@pytest.mark.parametrize("state", ["ambiguous", "cited"])
@pytest.mark.parametrize("order", [
    pytest.param("ABA", id="A-B-A"),
    pytest.param("AAB", id="A-A-B"),
    pytest.param("ABAB", id="A-B-A-B"),
])
def test_repeated_numeric_citations_keep_two_distinct_candidates_in_first_seen_order(state, order):
    citations = tuple({"A": A, "B": B}[k] for k in order)
    b = _bundle(citations, sources=[source(TEXT)]) if state == "ambiguous" else _cited(citations)
    frozen = b.model_dump_json()
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    AtomEvidenceManifest.model_validate(m.model_dump(mode="json"))
    row = _row(m, "1,150")
    assert row.evidence_state == "ambiguous"
    assert row.candidate_target_ids == [citation_target_id(b, A), citation_target_id(b, B)]
    assert row.citation_target_ids == []
    assert RESTATED in row.reason
    assert _row(m, "24.6%").evidence_state == "located"  # the sibling atom is still usable
    assert b.model_dump_json() == frozen  # the frozen span keeps its raw citations
    assert [c.start_line for c in next(s for s in b.spans if s.text == "1,150").citations] == [
        {"A": A, "B": B}[k].start_line for k in order]


@pytest.mark.parametrize("citations", [
    pytest.param((), id="none"),
    pytest.param((A,), id="one"),
    pytest.param((A, A), id="repeat-only"),
    pytest.param((A, A, A), id="repeat-only-thrice"),
])
def test_fewer_than_two_distinct_numeric_candidates_stay_unavailable(citations):
    b = _bundle(citations, sources=[source(TEXT)])
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    row = _row(m, "1,150")
    assert row.evidence_state == "unavailable"
    assert row.candidate_target_ids == [] and row.citation_target_ids == []
