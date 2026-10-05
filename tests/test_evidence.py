"""Evidence completeness: nothing numeric or unresolved may disappear."""

from evidence_review import validate_bundle
from evidence_review.contracts import Claim, ReferenceItem
from evidence_review.evidence import evidence_views, inventory, numeric_spans, passage
from synthetic import build, margin_claim, source, unavailable


def texts(text, path="summary"):
    return [(n.text, n.state) for n in numeric_spans(path, text)]


def test_quantity_classes_each_remain_separate_occurrences():
    assert texts("Revenue fell (3.5) million, -$4m and -2.5% to $1.2 billion.") == [
        ("(3.5)", "uncited"),
        ("-$4m", "uncited"),
        ("-2.5%", "uncited"),
        ("$1.2 billion", "uncited"),
    ]
    assert texts("Guidance of 10–12% and 180 basis points; leverage 1.5x; split 3:1.") == [
        ("10–12%", "uncited"),
        ("180 basis points", "uncited"),
        ("1.5x", "uncited"),
        ("3:1", "uncited"),
    ]
    assert texts("€3bn, ¥200, 5,000k and 4.5 percent.") == [
        ("€3bn", "uncited"),
        ("¥200", "uncited"),
        ("5,000k", "uncited"),
        ("4.5 percent", "uncited"),
    ]


def test_dates_and_identifiers_are_visible_but_classified():
    spans = numeric_spans("summary", "Filed 2026-03-31 (March 31, 2026); 10-K Item 7, CIK 0000320193, Q3.")
    assert [(n.text, n.state) for n in spans] == [
        ("2026-03-31", "identifier"),
        ("March 31, 2026", "identifier"),
        ("10", "identifier"),
        ("7", "identifier"),
        ("0000320193", "identifier"),
        ("3", "identifier"),
    ]
    assert all(n.reason for n in spans)


def test_repeated_values_are_not_deduplicated_across_fields_or_claims():
    b = build(
        [
            ("summary", "Margin reached 24.6%.", ["margin"]),
            ("qualifications", "The 24.6% figure excludes a restatement.", []),
        ],
        [margin_claim()],
        {("summary", "24.6%"): ("derived", ["margin"])},
    )
    repeated = [n for n in b.spans if n.text == "24.6%"]
    assert len(repeated) == 2
    assert {(n.field_path, n.state) for n in repeated} == {
        ("summary", "derived"),
        ("qualifications", "uncited"),
    }
    assert inventory(b)["spans_by_state"] == {"derived": 1, "uncited": 1}


def test_margin_arithmetic_and_source_support_are_distinct():
    b = build([("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"])], [margin_claim()])
    r = b.claims[0].calculation.recomputation
    assert (r["status"], r["result"], r["evidence_verified"]) == ("match", "180.0", False)
    assert r["evidence"]["status"] == "all_operands_cited"
    # Remove prior-period evidence: arithmetic still displays, support is unresolved.
    missing = build(
        [("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"])],
        [margin_claim(unavailable())],
    )
    r = missing.claims[0].calculation.recomputation
    assert r["status"] == "match"
    assert r["evidence"] == {
        "status": "operand_evidence_missing",
        "missing": ["prior"],
        "warnings": [],
        "note": "Arithmetic and located inputs do not establish that the claim is supported.",
    }
    assert inventory(missing)["unresolved_subjects"] == ["margin"]
    # A producer cannot inject its own recomputation verdict.
    raw = missing.model_dump(mode="json")
    raw["claims"][0]["calculation"]["recomputation"] = {"status": "match", "evidence": {"status": "all_operands_cited"}}
    assert validate_bundle(raw).claims[0].calculation.recomputation["evidence"]["status"] == "operand_evidence_missing"


def test_unit_and_period_gaps_are_flagged_not_reconciled():
    raw = build([("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"])], [margin_claim()]).model_dump(mode="json")
    raw["claims"][0]["calculation"]["operands"][1].update(unit="bps", period="")
    b = validate_bundle(raw)
    warnings = b.claims[0].calculation.recomputation["evidence"]["warnings"]
    assert "operands use different units without a declared conversion" in warnings
    assert "operand period unavailable" in warnings


def test_passage_returns_table_header_notes_and_paragraph():
    view = passage(source(), 7, 7)
    roles = {row["line"]: row["role"] for row in view["lines"]}
    assert roles[4] == "table_header" and roles[5] == "table_header"
    assert roles[7] == "cited"
    assert roles[8] == "note"
    assert view["table_header_is_heuristic"] is True
    prose = passage(source(), 10, 10)
    assert [r["role"] for r in prose["lines"] if r["line"] == 10] == ["cited"]
    assert "Footnote" not in view["text"]


def test_prose_claims_and_omissions_have_evidence_access_without_numbers():
    prose = Claim(claim_id="outlook", text="Management expects demand to soften.", citations=[])
    omission = ReferenceItem(reference_id="ref-restatement", text="The revenue restatement is disclosed.")
    b = build([("summary", "Demand is expected to soften.", ["outlook"])], [prose], references=[omission])
    assert not b.spans
    summary = inventory(b)
    assert summary["unresolved_subjects"] == ["outlook", "ref-restatement"]
    assert summary["span_total"] == 0


def test_evidence_views_cover_every_located_citation():
    b = build([("summary", "Operating margin rose 180 bps to 24.6%.", ["margin"])], [margin_claim()])
    views = evidence_views(b)
    assert set(views) == {"filing:6:6"}
    assert "full_text" not in views["filing:6:6"]
    assert inventory(b)["calculations_not_matching"] == []
