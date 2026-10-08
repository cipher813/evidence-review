"""Audit regressions (evidence-review-I26, I28): atom targets and rendered locations are bound to their own evidence.

Every value is synthetic. These reuse the auditor's exact repro inputs: a Revenue 10 quantity must not navigate
to the bundle's Headcount 99 line, an HTML "Headcount was 10 people." must not be an exact location for a frozen
"Revenue was 10 units.", and a literal ``#Heading`` line must render once within a tight budget.
"""
import pytest

from evidence_review.atomic_evidence import atom_view, build_atom_manifest, citation_target_id, validate_atom_evidence
from evidence_review.contracts import Claim, PreparedEvidence, validate_bundle
from evidence_review.source_rendering import OriginalAsset, RenderLimits, prepare_render_asset, render_frozen_text
from rendered_fixtures import FACT, STATEMENT, html_bundle, outlook_fact, read
from synthetic import build, located, source

REVENUE = "Revenue was 10 units.\nHeadcount was 99 people.\n"


def revenue_bundle(text=REVENUE, claim_cites=None, span_cites=None):
    """The auditor's A01/A02 input: the claim cites both lines, the number's own span cites line 1 only."""
    claim_cites = claim_cites if claim_cites is not None else [located(1, "10"), located(2, "99")]
    b = build([("summary", "Revenue was 10 units.", ["c"])],
              [Claim(claim_id="c", text="Revenue was 10 units.", citations=claim_cites)], sources=[source(text)])
    d = b.model_dump(mode="json")
    d["spans"][0].update(state="cited", claim_ids=["c"],
                         citations=[c.model_dump(mode="json") for c in (span_cites or [located(1, "10")])])
    return validate_bundle(d)


# ---------------------------------------------------------------- atom-to-evidence binding


def test_unrelated_known_target_from_the_global_pool_is_rejected():
    b = revenue_bundle()
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m)
    own, other = citation_target_id(b, located(1, "10")), citation_target_id(b, located(2, "99"))
    assert m.items[0].citation_target_ids == [own]
    view = atom_view(b, m)
    assert view["rows"][0]["targets"][0]["binding"] == "span_citation"
    d = m.model_dump(mode="json")
    d["items"][0]["citation_target_ids"] = [other]  # Headcount 99: known to the bundle, not this number's evidence.
    with pytest.raises(ValueError, match="not bound to this atom's own evidence"):
        validate_atom_evidence(b, d)
    d["items"][0].update(evidence_state="ambiguous", citation_target_ids=[], candidate_target_ids=[own, other],
                         reason="two lines")
    with pytest.raises(ValueError, match="not bound to this atom's own evidence"):
        validate_atom_evidence(b, d)


def test_explicitly_attributed_prepared_evidence_is_an_accepted_and_labelled_binding():
    b0 = revenue_bundle(span_cites=[located(1, "10")])
    d = b0.model_dump(mode="json")
    d["spans"][0].update(state="uncited", citations=[], prepared_evidence=[PreparedEvidence(
        citations=[located(1, "Revenue was 10 units.")], reason="Independently located row").model_dump(mode="json")])
    b = validate_bundle(d)
    m = build_atom_manifest(b).model_dump(mode="json")
    prepared = citation_target_id(b, located(1, "Revenue was 10 units."))
    m["items"][0].update(evidence_state="located", citation_target_ids=[prepared], reason="")
    manifest = validate_atom_evidence(b, m)
    assert atom_view(b, manifest)["rows"][0]["targets"][0]["binding"] == "prepared_evidence"
    # The same claim-level line, never attributed to this number, is still refused.
    m["items"][0]["citation_target_ids"] = [citation_target_id(b, located(2, "99"))]
    with pytest.raises(ValueError, match="not bound"):
        validate_atom_evidence(b, m)


def test_unrelated_calculation_is_rejected_and_own_calculation_navigates():
    b = html_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    validate_atom_evidence(b, m)
    d = m.model_dump(mode="json")
    rows = {i["text"]: i for i in d["items"]}
    assert rows["180 bps"]["calculation_ref"] == "span:" + rows["180 bps"]["numeric_span_id"]
    # 24.6% borrowing the 180 bps span's calculation; 180 bps borrowing the claim-level one.
    for text, ref in (("24.6%", rows["180 bps"]["calculation_ref"]), ("180 bps", "claim:margin")):
        bad = m.model_dump(mode="json")
        next(i for i in bad["items"] if i["text"] == text)["calculation_ref"] = ref
        with pytest.raises(ValueError, match="not its own occurrence's calculation"):
            validate_atom_evidence(b, bad)
    # A fact may use its own statement's calculation, or a number written inside its words, nothing else.
    fact = next(i for i in d["items"] if i["kind"] == "fact")
    fact["calculation_ref"] = "claim:margin"
    validate_atom_evidence(b, d)
    fact["calculation_ref"] = rows["180 bps"]["calculation_ref"]
    with pytest.raises(ValueError, match="not its own occurrence's calculation"):
        validate_atom_evidence(b, d)


def test_fact_targets_are_its_statement_citations_or_citations_declared_for_that_atom():
    b = html_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    fact = next(i for i in m.items if i.kind == "fact")
    (declared,) = m.citations
    assert declared.atom_ids == [fact.atom_id]
    row = next(r for r in atom_view(b, m)["rows"] if r["kind"] == "fact")
    assert row["targets"][0]["binding"] == "declared_atom_citation"
    d = m.model_dump(mode="json")
    d["citations"][0]["atom_ids"] = []  # Declared, but attributed to no atom: global pool, not this fact's.
    with pytest.raises(ValueError, match="not bound"):
        validate_atom_evidence(b, d)
    d["citations"][0]["atom_ids"] = [fact.atom_id, "atom:" + "0" * 24]
    with pytest.raises(ValueError, match="unknown fact atom"):
        validate_atom_evidence(b, d)
    # The statement's own located citation is a valid binding without any declaration.
    d = m.model_dump(mode="json")
    own = citation_target_id(b, located(7, "24.6%"))
    next(i for i in d["items"] if i["kind"] == "fact")["citation_target_ids"] = [own]
    validated = validate_atom_evidence(b, d)
    row = next(r for r in atom_view(b, validated)["rows"] if r["kind"] == "fact")
    assert row["targets"][0]["binding"] == "linked_statement_citation"


# ---------------------------------------------------------------- frozen-to-original correspondence


def html_target(frozen, raw, cite):
    b = revenue_bundle(frozen, claim_cites=[cite], span_cites=[cite])
    (t,) = prepare_render_asset(b, "filing", OriginalAsset(raw, "text/html", frozen_derived=False)).manifest.targets
    return t


def test_same_number_under_a_different_metric_is_not_exact_even_as_a_single_hit():
    good = html_target(REVENUE, b"<p>Revenue was 10 units.</p>", located(1, "10"))
    assert good.status == "exact" and good.dom_targets
    bad = html_target(REVENUE, b"<p>Headcount was 10 people.</p>", located(1, "10"))
    assert bad.status == "unavailable" and not bad.dom_targets and "does not match the cited frozen line" in bad.reason


def test_same_number_in_a_different_period_is_not_exact():
    frozen = "Revenue in FY2026 was 10 units.\n"
    assert html_target(frozen, b"<p>Revenue in FY2026 was 10 units.</p>", located(1, "10")).status == "exact"
    assert html_target(frozen, b"<p>Revenue in FY2025 was 10 units.</p>", located(1, "10")).status == "unavailable"


TABLE = "| Metric | FY2025 | FY2026 |\n| --- | --- | --- |\n| Revenue | 9 | 10 |\n"


def html_table(header, label):
    return (f"<table><thead><tr><th>Metric</th><th>{header[0]}</th><th>{header[1]}</th></tr></thead>"
            f"<tbody><tr><th>{label}</th><td>9</td><td>10</td></tr></tbody></table>").encode()


def test_table_cell_needs_its_row_and_header_to_match_the_frozen_table():
    cite = located(3, "10")
    assert html_target(TABLE, html_table(("FY2025", "FY2026"), "Revenue"), cite).status == "exact"
    assert html_target(TABLE, html_table(("FY2024", "FY2025"), "Revenue"), cite).status == "unavailable"  # period
    assert html_target(TABLE, html_table(("FY2025", "FY2026"), "Headcount"), cite).status == "unavailable"  # metric


def test_pdf_single_hit_in_another_period_is_not_exact():
    raw = read("two-page.pdf")

    def pdf_target(line):
        b = revenue_bundle(line + "\n", claim_cites=[located(1, "1,150")], span_cites=[located(1, "1,150")])
        (t,) = prepare_render_asset(b, "filing", OriginalAsset(raw, "application/pdf")).manifest.targets
        return t
    good = pdf_target("Revenue was 1,150 in FY2026.")
    assert (good.status, good.page) == ("exact", 2)
    bad = pdf_target("Revenue was 1,150 in FY2025.")
    assert bad.status == "unavailable" and "does not match" in bad.reason


def test_correct_fixture_mappings_still_navigate_exactly():
    b = html_bundle()
    m = build_atom_manifest(b, [outlook_fact(b)])
    asset = prepare_render_asset(b, "filing", OriginalAsset(read("table.html"), "text/html"), m)
    by_key = {(t.start_line, t.excerpt): t for t in asset.manifest.targets}
    for key in ((7, "24.6%"), (7, "22.8%"), (8, "1,150"), (12, FACT)):
        assert by_key[key].status == "exact" and len(by_key[key].dom_targets) == 1, key
    assert by_key[(7, "24.6%")].dom_targets != by_key[(7, "22.8%")].dom_targets
    rows = atom_view(b, validate_atom_evidence(b, m, [asset.manifest]), [asset.manifest])["rows"]
    margin = next(r for r in rows if r["text"] == "24.6%")
    assert margin["targets"][0]["status"] == "exact" and margin["targets"][0]["rendered"]
    assert STATEMENT.count("24.6%") == 1


# ---------------------------------------------------------------- Markdown progress invariant (I28)


@pytest.mark.parametrize("first", ["#Heading", "####### Seven hashes", "   # Indented hash", "#", "# Heading"])
def test_hash_prefixed_lines_render_once_within_a_tight_budget(first):
    text = first + "\nRevenue was 10 units.\n"
    b = build([("summary", "Revenue was 10 units.", ["c"])],
              [Claim(claim_id="c", text="Revenue was 10 units.", citations=[located(2, "10")])], sources=[source(text)])
    asset = render_frozen_text(b, "filing", limits=RenderLimits(max_work=2, max_nodes=3))
    nodes = asset.derivative["nodes"]
    leaves = [n for top in nodes for n in [top, *top.get("children", [])] if "line" in n]
    assert sorted(n["line"] for n in leaves) == [1, 2]  # Each frozen line rendered exactly once.
    first_node = next(n for n in leaves if n["line"] == 1)
    if first == "# Heading":
        assert (first_node["tag"], first_node["text"]) == ("h1", "Heading")
    else:
        assert first_node["tag"] == "line" and first_node["text"] == first  # Literal, not a heading.
    (t,) = asset.manifest.targets
    assert t.status == "exact" and t.dom_targets == [next(n["id"] for n in leaves if n["line"] == 2)]
