"""Backward-compatible context, prepared provenance and recursive arithmetic."""
import pytest
from pydantic import ValidationError
from evidence_review.contracts import Calculation, Operand, ReviewBundle
from evidence_review.example import example_bundle
from synthetic import located, unavailable


def test_preparation_stays_separate_hash_bound_and_old_bundles_still_load():
    b = example_bundle()
    old = b.model_dump(mode="json")
    b2 = ReviewBundle.model_validate(old)
    span = next(n for n in b2.spans if n.state != "identifier")
    raw = b2.model_dump(mode="json")
    n = next(n for n in raw["spans"] if n["span_id"] == span.span_id)
    n["context"] = {"metric": "Margin", "entity": "Synthetic", "period": "FY2024", "unit": "pct", "status": "resolved"}
    n["prepared_evidence"] = [{"origin": "independently_located", "citations": [], "reason": "Prepared, not supplied by the answer"}]
    prepared = ReviewBundle.model_validate(raw)
    assert prepared.bundle_hash != b.bundle_hash
    assert prepared.spans[0].citations == b.spans[0].citations
    assert prepared.spans[0].context.metric == "Margin"


def test_unresolved_inputs_have_no_recomputed_result_and_constants_need_no_source():
    c = Calculation(formula="a * scale", operands=[Operand(name="a", value="18.2"), Operand(name="scale", value="100", kind="constant")], result="1820")
    assert c.recomputation["status"] == "unresolved"
    assert c.recomputation["result"] is None
    assert c.recomputation["evidence"]["missing"] == ["a"]
    assert c.recomputation["arithmetic"]["status"] == "match"


def test_nested_input_exposes_its_sources_and_disagreement():
    inner = Calculation(formula="a - b", operands=[Operand(name="a", value="24.6", citation=located(6, "24.6%")), Operand(name="b", value="22.8", citation=located(6, "22.8%"))], result="1.8")
    c = Calculation(formula="delta * 100", operands=[Operand(name="delta", value="1.8", kind="derived", calculation=inner)], result="180")
    assert c.recomputation["status"] == "match"
    broken = c.model_dump(mode="json")
    broken["operands"][0]["value"] = "9"
    bad = Calculation.model_validate(broken)
    assert bad.recomputation["status"] == "unresolved"
    assert bad.recomputation["evidence"]["missing"] == ["delta"]


def test_source_operand_cannot_claim_to_be_a_constant_or_derived_without_provenance():
    with pytest.raises(ValidationError):
        Operand(name="a", value="1", kind="constant", citation=unavailable())
    with pytest.raises(ValidationError):
        Operand(name="a", value="1", kind="derived")


def test_prepared_citations_validate_against_frozen_source():
    raw = example_bundle().model_dump(mode="json")
    raw["spans"][0]["prepared_evidence"] = [{"origin": "independently_located", "citations": [located(999, "99").model_dump()]}]
    with pytest.raises(ValidationError):
        ReviewBundle.model_validate(raw)


def test_workload_counts_are_caller_owned_and_bounds_checked():
    raw = example_bundle().model_dump(mode="json")
    raw["workload"] = {"answer_index": 1, "assigned_answers": 3, "task_counts": {"independent": 3}, "assignment_reason": "Seeded sample", "expansion_conditions": ["Failed acceptance may expand later"]}
    assert ReviewBundle.model_validate(raw).workload.assigned_answers == 3
    raw["workload"]["answer_index"] = 4
    with pytest.raises(ValidationError):
        ReviewBundle.model_validate(raw)
