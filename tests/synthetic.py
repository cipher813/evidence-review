"""Artificial bundle builder for tests; every value is invented."""

from evidence_review.contracts import (
    Calculation,
    Citation,
    Claim,
    FormField,
    Operand,
    ReportField,
    ReviewBundle,
    Source,
    digest,
)
from evidence_review.evidence import numeric_spans

TABLE = (
    "# Synthetic issuer results\n"
    "Prepared for testing only.\n"
    "\n"
    "| Metric | FY2025 | FY2026 |\n"
    "| --- | --- | --- |\n"
    "| Operating margin | 22.8% | 24.6% |\n"
    "| Revenue ($m) | 1,200 | 1,150 |\n"
    "(1) Revenue restated for a disposed segment.\n"
    "\n"
    "Management expects demand to soften next year.\n"
)


def source(text=TABLE, source_id="filing"):
    return Source(source_id=source_id, title="Synthetic filing", text=text, sha256=digest(text))


def located(line, excerpt, source_id="filing"):
    return Citation(source_id=source_id, start_line=line, end_line=line, excerpt=excerpt, status="located")


def unavailable(reason="operand not found in frozen sources"):
    return Citation(source_id="filing", status="unavailable", reason=reason)


def margin_claim(prior_citation=None):
    current = located(6, "24.6%")
    prior = prior_citation or located(6, "22.8%")
    return Claim(
        claim_id="margin",
        text="Operating margin rose 180 bps to 24.6%.",
        citations=[c for c in (prior, current) if c.status == "located"],
        calculation=Calculation(
            formula="(current - prior) * 100",
            operands=[
                Operand(name="current", value="24.6", unit="pct", period="FY2026", citation=current),
                Operand(name="prior", value="22.8", unit="pct", period="FY2025", citation=prior),
            ],
            result="180",
            unit="bps",
            tolerance="0.01",
        ),
    )


def build(fields, claims, mapping=None, sources=None, form=None, references=(), task_kind="independent", bundle_id="synthetic"):
    """fields: [(path, text, claim_ids)]; mapping: {(path, span_text): (state, claim_ids)}."""
    report = [ReportField(path=p, label=p.title(), text=t, claim_ids=list(c)) for p, t, c in fields]
    spans = [n for f in report for n in numeric_spans(f.path, f.text)]
    for n in spans:
        if mapping and (n.field_path, n.text) in mapping:
            n.state, n.claim_ids = mapping[(n.field_path, n.text)][0], list(mapping[(n.field_path, n.text)][1])
    return ReviewBundle(
        bundle_id=bundle_id,
        task_kind=task_kind,
        document_hashes={"report": digest([f.model_dump() for f in report])},
        fields=report,
        sources=sources or [source()],
        spans=spans,
        claims=claims,
        references=list(references),
        form=form
        or [
            FormField(
                field_id="report_complete",
                label="I reviewed the full report.",
                kind="boolean",
                require_true=True,
            )
        ],
    )
