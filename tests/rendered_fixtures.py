"""Synthetic bundles bound to the rendered-source fixtures (every value invented)."""
from pathlib import Path

from evidence_review.atomic_evidence import build_atom_manifest
from evidence_review.contracts import AtomBinding, Calculation, Claim, FormField, Operand, atom_identity, validate_bundle
from evidence_review.source_rendering import OriginalAsset, prepare_render_asset
from synthetic import build, located, source

FIXTURES = Path(__file__).parent / "fixtures" / "rendered-sources"
STATEMENT = ("Operating margin was 24.6% in FY2026, up 180 bps, on revenue of 1,150. "
             "Management expects demand to soften next year.")
FACT = "Management expects demand to soften next year"


def read(name):
    return (FIXTURES / name).read_bytes()


def html_bundle(task_kind="reference", with_checks=True):
    """A source-check task whose frozen text was normalized from table.html."""
    frozen = read("table.frozen.md").decode()
    margin = located(7, "24.6%")
    prior = located(7, "22.8%")
    claim = Claim(claim_id="margin", text=STATEMENT, citations=[margin],
                  calculation=Calculation(formula="(current - prior) * 100", operands=[
                      Operand(name="current", value="24.6", unit="pct", period="FY2026", citation=margin),
                      Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=prior)],
                      result="180", unit="bps", tolerance="0.01"))
    b = build([("summary", STATEMENT, ["margin"])], [claim], sources=[source(frozen)], task_kind=task_kind,
              bundle_id="atomic-" + task_kind)
    data = b.model_dump(mode="json")
    for s in data["spans"]:
        if s["text"] == "24.6%":
            s.update(state="cited", claim_ids=["margin"], citations=[margin.model_dump(mode="json")])
        elif s["text"] == "180 bps":
            s.update(state="derived", claim_ids=["margin"], calculation=claim.calculation.model_dump(mode="json"))
        elif s["text"] == "1,150":
            s.update(state="cited", claim_ids=["margin"], citations=[located(8, "1,150").model_dump(mode="json")])
    form = [dict(field_id="verdict:margin", label="Source verdict", options=["verified", "incorrect", "cannot_verify"],
                 required=True, subject_id="margin", numeric_verification_values=["verified"],
                 note_required_unless=["verified"]),
            dict(field_id="report_complete", label="I checked every assigned atom.", kind="boolean",
                 require_true=True)]
    if with_checks:
        for s in data["spans"]:
            if s["state"] != "identifier":
                form.append(dict(field_id="quantity:" + s["span_id"], label="Checked " + s["text"], kind="boolean",
                                 required=False, subject_id="margin", numeric_span_id=s["span_id"]))
        text = STATEMENT
        start = text.index(FACT)
        atom_id = atom_identity(b.bundle_id, b.document_hashes, "summary", text, start, start + len(FACT), "fact")
        form.append(FormField(field_id="fact:outlook", label="Checked: " + FACT, kind="boolean", required=False,
                              subject_id="margin", atom=AtomBinding(atom_id=atom_id, field_path="summary",
                                                                    start=start, end=start + len(FACT), text=FACT)
                              ).model_dump(mode="json"))
    data["form"] = form
    return validate_bundle(data)


def outlook_fact(bundle, line=12):
    start = STATEMENT.index(FACT)
    return {"field_path": "summary", "start": start, "end": start + len(FACT), "claim_ids": ["margin"],
            "citations": [located(line, FACT)]}


def html_sidecars(bundle):
    atoms = build_atom_manifest(bundle, [outlook_fact(bundle)])
    asset = prepare_render_asset(bundle, "filing", OriginalAsset(read("table.html"), "text/html"), atoms)
    return atoms, asset
