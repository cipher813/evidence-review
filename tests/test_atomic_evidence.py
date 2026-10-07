"""atom-evidence/v1 and source-render-manifest/v1: one row per atom, hash-bound, never a verdict."""
import json
from pathlib import Path

import pytest

from evidence_review import FileStore
from evidence_review.atomic_evidence import (
    AtomEvidenceManifest,
    SourceRenderManifest,
    atom_view,
    build_atom_manifest,
    citation_target_id,
    validate_atom_evidence,
    validate_render_manifest,
)
from evidence_review.contracts import AtomBinding, FormField, atom_identity, digest, validate_bundle
from evidence_review.example import example_bundle
from synthetic import build, located, margin_claim, source

STATEMENT = ("Operating margin rose 180 bps to 24.6% while revenue fell to $1,150m. "
             "Management expects demand to soften next year.")
FACT = "Management expects demand to soften next year"
COMPOUND = "revenue fell"
TWIN = "| Ratio A | 24.6% | 24.6% |\n"


def bundle(form_extra=(), text=STATEMENT, sources=None):
    b = build([("summary", text, ["margin"])], [margin_claim()], sources=sources)
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        if s["text"] == "180 bps":
            s.update(state="derived", claim_ids=["margin"],
                     calculation=margin_claim().calculation.model_dump(mode="json"))
        elif s["text"] == "24.6%":
            s.update(state="cited", claim_ids=["margin"], citations=[located(6, "24.6%").model_dump(mode="json")])
        elif s["text"] == "$1,150m":
            s.update(state="unavailable", reason="Unit scale not stated in the source row")
    data["form"].extend(f.model_dump(mode="json") if hasattr(f, "model_dump") else f for f in form_extra)
    return validate_bundle(data)


def fact(b, phrase=FACT, **extra):
    text = b.fields[0].text
    start = text.index(phrase)
    return {"field_path": "summary", "start": start, "end": start + len(phrase), "claim_ids": ["margin"], **extra}


def fact_check(b, phrase=FACT, field_id="fact:outlook"):
    text = b.fields[0].text
    start = text.index(phrase)
    atom_id = atom_identity(b.bundle_id, b.document_hashes, "summary", text, start, start + len(phrase), "fact")
    return FormField(field_id=field_id, label="Checked: " + phrase, kind="boolean", required=False,
                     subject_id="margin", atom=AtomBinding(atom_id=atom_id, field_path="summary", start=start,
                                                           end=start + len(phrase), text=phrase))


def test_one_row_per_atom_quantities_and_declared_facts_reconcile():
    b = bundle()
    m = build_atom_manifest(b, [fact(b, citations=[located(10, "Management expects demand to soften next year.")]),
                                fact(b, COMPOUND, evidence_state="unsupported", reason="No source states a fall")])
    validate_atom_evidence(b, m)
    rows = {(i.kind, i.text): i for i in m.items}
    assert set(rows) == {("quantity", "180 bps"), ("quantity", "24.6%"), ("quantity", "$1,150m"),
                         ("fact", FACT), ("fact", COMPOUND)}
    assert rows[("quantity", "180 bps")].evidence_state == "derived"
    assert rows[("quantity", "180 bps")].calculation_ref.startswith("span:")
    assert rows[("quantity", "24.6%")].evidence_state == "located"
    assert rows[("quantity", "$1,150m")].evidence_state == "unavailable"
    assert rows[("fact", FACT)].evidence_state == "located"
    assert rows[("fact", COMPOUND)].reason
    # One statement holding several facts produces several rows; the claim id never stands in for them.
    assert all(i.claim_ids == ["margin"] for i in m.items if i.text != "$1,150m")


def test_repeated_equal_numbers_have_distinct_occurrence_identities():
    b = bundle(text="Margin was 24.6% in FY2026 and 24.6% again on a restated basis.")
    m = build_atom_manifest(b)
    ids = [i.atom_id for i in m.items if i.text == "24.6%"]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_ambiguous_citation_keeps_every_candidate_and_never_chooses():
    text = source().text + TWIN
    b = bundle(sources=[source(text)])
    twin = len(text.splitlines())
    cand = [located(6, "24.6%"), located(twin, "24.6%")]
    m = build_atom_manifest(b, [fact(b, citations=cand, reason="Two rows hold this wording")])
    validate_atom_evidence(b, m)
    row = next(i for i in m.items if i.kind == "fact")
    assert row.evidence_state == "ambiguous" and len(row.candidate_target_ids) == 2
    assert row.citation_target_ids == []


def test_missing_evidence_stays_visible_with_its_reason():
    b = bundle()
    m = build_atom_manifest(b, [fact(b, reason="No eligible source discusses outlook")])
    validate_atom_evidence(b, m)
    row = next(i for i in m.items if i.kind == "fact")
    assert row.evidence_state == "unavailable" and row.reason
    view = atom_view(b, m)
    assert "never checks a row" in view["verification_note"]


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["items"][0].update(start=d["items"][0]["start"] + 1), "offsets"),
    (lambda d: d["items"][0].update(text="999"), "offsets"),
    (lambda d: d["items"][0].update(atom_id="atom:" + "0" * 24), "identity"),
    (lambda d: d.update(bundle_hash="x" * 64), "different bundle"),
    (lambda d: d["items"][1].update(evidence_state="located", citation_target_ids=["tgt:" + "1" * 24]), "unknown citation target"),
    (lambda d: d["items"].pop(0), "omits 1"),
    (lambda d: d["items"].append(dict(d["items"][0])), "duplicate atom"),
    (lambda d: d["items"][0].update(claim_ids=["invented"]), "unknown claim"),
    (lambda d: d["items"][0].update(calculation_ref="span:missing"), "unknown calculation"),
    (lambda d: d["atomization"].update(scope_fields=["nope"]), "non-answer"),
    (lambda d: d["items"][0].update(form_field_id="report_complete"), "check"),
])
def test_malformed_manifest_fails_validation(mutate, message):
    b = bundle()
    data = build_atom_manifest(b).model_dump(mode="json")
    mutate(data)
    with pytest.raises(ValueError, match=message):
        validate_atom_evidence(b, data)


def test_overlapping_or_number_shaped_fact_atoms_are_refused():
    b = bundle()
    overlapping = [fact(b, "Management expects", reason="none"), fact(b, "expects demand", reason="none")]
    with pytest.raises(ValueError, match="overlapping"):
        validate_atom_evidence(b, build_atom_manifest(b, overlapping))
    with pytest.raises(ValueError, match="bare number"):
        validate_atom_evidence(b, build_atom_manifest(b, [fact(b, "24.6%", reason="none")]))


def test_assigned_coverage_lists_every_other_number_as_context_not_dropped():
    b = bundle()
    target = next(s.span_id for s in b.spans if s.text == "24.6%")
    m = build_atom_manifest(b, coverage="assigned", assigned_span_ids=[target])
    validate_atom_evidence(b, m)
    assert [i.text for i in m.items] == ["24.6%"]
    assert len(m.atomization.context_span_ids) == 2
    data = m.model_dump(mode="json")
    data["atomization"]["context_span_ids"] = data["atomization"]["context_span_ids"][:1]
    with pytest.raises(ValueError, match="omits 1"):
        validate_atom_evidence(b, data)
    data["atomization"].update(coverage="complete")
    with pytest.raises(ValueError, match="complete coverage"):
        validate_atom_evidence(b, data)


def test_fact_check_binding_is_validated_and_gates_verified_verdicts(tmp_path):
    b0 = bundle()
    check = fact_check(b0)
    data = b0.model_dump(mode="json")
    data["form"].insert(0, dict(field_id="support:margin", label="Support", options=["supported", "unsupported"],
                                required=False, subject_id="margin"))
    data["form"][0]["numeric_verification_values"] = ["supported"]
    data["form"].append(check.model_dump(mode="json"))
    b = validate_bundle(data)
    m = build_atom_manifest(b, [fact(b, citations=[located(10, "Management expects demand to soften next year.")])])
    validate_atom_evidence(b, m)
    assert next(i for i in m.items if i.kind == "fact").form_field_id == "fact:outlook"
    store = FileStore(tmp_path)
    store.register(b)
    answers = {"judgments": {"support:margin": {"value": "supported"}, "report_complete": {"value": True}},
               "defects": []}
    with pytest.raises(ValueError, match="atom"):
        store.save_submission(b, 0, "k1", answers, "Ada", 1)
    answers["judgments"]["support:margin"] = {"value": "unsupported", "note": "Outlook not sourced"}
    store.save_submission(b, 0, "k2", answers, "Ada", 1)  # partial/negative is never blocked by an unchecked atom
    # A fact check whose binding text disagrees with the frozen answer is refused at bundle validation.
    bad = b.model_dump(mode="json")
    bad["form"][-1]["atom"]["text"] = "Management expects"
    with pytest.raises(ValueError, match="atom"):
        validate_bundle(bad)
    # Removing the row for a bound check orphans the control.
    rows = m.model_dump(mode="json")
    rows["items"] = [i for i in rows["items"] if i["kind"] != "fact"]
    with pytest.raises(ValueError, match="fact check has no atom row"):
        validate_atom_evidence(b, rows)


def test_legacy_bundle_hash_and_quantity_binding_unchanged_by_atom_extension():
    b = example_bundle()
    original = b.model_dump(mode="json")
    assert all("atom" not in f for f in original["form"])
    assert validate_bundle(original).bundle_hash == b.bundle_hash
    fixture = Path(__file__).parent / "fixtures" / "diagnostic-free-v041"
    payload = json.loads((fixture / "bundle.json").read_text())
    assert validate_bundle(payload).bundle_hash == json.loads((fixture / "provenance.json").read_text())["bundle_hash"]


def test_render_manifest_binds_bundle_source_and_targets():
    b = bundle()
    src = b.sources[0]
    cite = located(6, "24.6%")
    tid = citation_target_id(b, cite)
    from evidence_review.atomic_evidence import RenderedSourceTarget
    target = RenderedSourceTarget(target_id=tid, source_id=src.source_id, frozen_sha256=src.sha256, start_line=6,
                                  end_line=6, excerpt="24.6%", dom_targets=["n6"], status="exact")
    payload = dict(source_id=src.source_id, bundle_hash=b.bundle_hash, frozen_sha256=src.sha256,
                   render_mode="normalized_snapshot", lineage="raw_unavailable",
                   fidelity_note="Readable rendering of the normalized frozen text; no original bytes.",
                   derivative_sha256="d" * 64, renderer={"name": "evidence-review", "version": "test"},
                   targets=[target.model_dump(mode="json")],
                   mapping_sha256=digest([target.model_dump(mode="json")]))
    manifest = validate_render_manifest(b, payload)
    m = build_atom_manifest(b)
    validate_atom_evidence(b, m, [manifest])
    row = next(r for r in atom_view(b, m, [manifest])["rows"] if r["text"] == "24.6%")
    assert row["targets"][0]["status"] == "exact" and row["targets"][0]["rendered"]
    for change, message in [({"bundle_hash": "0" * 64}, "different bundle"),
                            ({"frozen_sha256": "0" * 64}, "another source|frozen hash"),
                            ({"mapping_sha256": "0" * 64}, "mapping hash"),
                            ({"source_id": "other"}, "another source|outside")]:
        with pytest.raises(ValueError, match=message):
            validate_render_manifest(b, {**payload, **change})
    with pytest.raises(ValueError, match="raw lineage"):
        validate_render_manifest(b, {**payload, "raw_sha256": "a" * 64})


def test_schemas_published_match_models():
    root = Path(__file__).parents[1]
    for name, model in [("atom-evidence-v1.json", AtomEvidenceManifest),
                        ("source-render-manifest-v1.json", SourceRenderManifest)]:
        expected = model.model_json_schema()
        for folder in (root / "schemas", root / "src" / "evidence_review" / "schemas"):
            assert json.loads((folder / name).read_text()) == expected
