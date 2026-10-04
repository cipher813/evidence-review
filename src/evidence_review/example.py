"""Artificial data only; a reproducible offline demo."""

from .contracts import (
    ReviewBundle,
    Source,
    ReportField,
    Claim,
    Citation,
    Calculation,
    Operand,
    FormField,
    digest,
)
from .evidence import numeric_spans


def example_bundle():
    text = "# Artificial issuer\n| Period | Margin |\n| --- | --- |\n| 2025 | 22.8% |\n| 2026 | 24.6% |\nFootnote: illustrative values only.\n"
    source = Source(
        source_id="demo-source",
        title="Synthetic margin table",
        text=text,
        sha256=digest(text),
    )
    prior = Citation(
        source_id=source.source_id,
        start_line=4,
        end_line=4,
        excerpt="22.8%",
        status="located",
    )
    current = Citation(
        source_id=source.source_id,
        start_line=5,
        end_line=5,
        excerpt="24.6%",
        status="located",
    )
    claim = Claim(
        claim_id="margin",
        text="Margin rose 180 bps to 24.6%.",
        citations=[prior, current],
        calculation=Calculation(
            formula="(current-prior)*100",
            operands=[
                Operand(
                    name="current",
                    value="24.6",
                    unit="pct",
                    period="2026",
                    citation=current,
                ),
                Operand(
                    name="prior",
                    value="22.8",
                    unit="pct",
                    period="2025",
                    citation=prior,
                ),
            ],
            result="180",
            unit="bps",
            tolerance="0.01",
        ),
    )
    fields = [
        ReportField(
            path="summary", label="Summary", text=claim.text, claim_ids=["margin"]
        ),
        ReportField(
            path="qualification",
            label="Qualification",
            text="An uncited 99% forecast needs checking.",
        ),
    ]
    spans = [s for f in fields for s in numeric_spans(f.path, f.text)]
    for s in spans:
        if s.field_path == "summary":
            s.state = "derived"
            s.claim_ids = ["margin"]
    return ReviewBundle(
        bundle_id="synthetic-margin",
        document_hashes={"report": digest([f.model_dump() for f in fields])},
        fields=fields,
        sources=[source],
        spans=spans,
        claims=[claim],
        form=[
            FormField(
                field_id="support:margin",
                label="Does the evidence support the margin claim?",
                options=[
                    "supported",
                    "partially supported",
                    "unsupported",
                    "contradicted",
                    "cannot determine",
                ],
                subject_id="margin",
                note_required_unless=["supported"],
            ),
            FormField(
                field_id="report_complete",
                label="I reviewed the full report and recorded all identified material defects.",
                kind="boolean",
            ),
        ],
    )
