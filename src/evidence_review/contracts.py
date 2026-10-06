"""Versioned neutral review contracts; no consumer-specific verdict policy."""

from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


PACKAGE_VERSION = "0.4.2"


def canonical_json(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value) -> str:
    data = (
        value.encode("utf-8")
        if isinstance(value, str)
        else json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    source_id: str
    title: str
    text: str
    sha256: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def integrity(self):
        if digest(self.text) != self.sha256:
            raise ValueError("source hash mismatch")
        return self


class Citation(Strict):
    source_id: str
    start_line: int | None = None
    end_line: int | None = None
    excerpt: str = ""
    status: Literal["located", "unavailable", "invalid_locator", "excerpt_mismatch"] = (
        "unavailable"
    )
    reason: str = ""


class Operand(Strict):
    name: str
    value: str
    unit: str = ""
    period: str = ""
    citation: Citation | None = None
    kind: Literal["source", "constant", "derived"] = "source"
    entity: str = ""
    calculation: Calculation | None = None

    @model_validator(mode="after")
    def provenance_kind(self):
        if self.kind == "constant" and (self.citation or self.calculation):
            raise ValueError("mathematical constants carry no source evidence")
        if self.kind == "derived" and (not self.calculation or self.citation):
            raise ValueError("derived input needs its calculation, not a direct citation")
        if self.kind == "source" and self.calculation:
            raise ValueError("source input cannot carry an intermediate calculation")
        return self


class Calculation(Strict):
    formula: str
    operands: list[Operand]
    result: str
    unit: str = ""
    tolerance: str = "0"
    conversions: list[str] = Field(default_factory=list)
    recomputation: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def recompute(self):
        from .evidence import calculate, operand_evidence

        # Derived on every validation, so a producer cannot supply its own verdict.
        arithmetic = calculate(
                self.formula,
                {o.name: o.value for o in self.operands},
                self.result,
                self.tolerance,
            )
        evidence = operand_evidence(self.operands, self.unit, self.conversions)
        self.recomputation = {**arithmetic, "evidence": evidence, "arithmetic": arithmetic}
        if evidence["missing"]:
            self.recomputation.update(status="unresolved", result=None,
                                     reason="Unresolved input evidence: " + ", ".join(evidence["missing"]))
        return self


Operand.model_rebuild()


class Claim(Strict):
    claim_id: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    calculation: Calculation | None = None


class ReportField(Strict):
    path: str
    label: str
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    # "context" is task material shown to the reviewer (the question, a cutoff),
    # not part of the answer under review; its numbers are never assertions.
    role: Literal["answer", "context"] = "answer"


class NumericContext(Strict):
    metric: str = ""
    entity: str = ""
    period: str = ""
    unit: str = ""
    status: Literal["resolved", "ambiguous", "unavailable"] = "unavailable"


class PreparedEvidence(Strict):
    origin: Literal["independently_located"] = "independently_located"
    citations: list[Citation] = Field(default_factory=list)
    calculation: Calculation | None = None
    reason: str = ""


class TechnicalDiagnostic(Strict):
    """Caller-attributed mechanical facts or limitations, never human verdicts."""
    scope: Literal["candidate_citation", "candidate_input", "candidate_arithmetic", "preparation"]
    outcome: Literal["consistent", "inconsistent", "unresolved"]
    reason: str = Field(min_length=1)
    input_path: list[str] = Field(default_factory=list)
    citation: Citation | None = None


class PreparationAttempt(Strict):
    method: str = Field(min_length=1)
    status: Literal["resolved", "unresolved"]
    diagnostics: list[TechnicalDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def attributed_failure(self):
        if self.status == "unresolved" and not self.diagnostics:
            raise ValueError("failed preparation requires an attributed reason")
        if self.status == "resolved" and any(d.outcome != "consistent" for d in self.diagnostics):
            raise ValueError("resolved attempt cannot carry failed diagnostics")
        if any(d.scope != "preparation" for d in self.diagnostics):
            raise ValueError("preparation attempt cannot carry candidate diagnostics")
        return self


class NumericDiagnostics(Strict):
    preparation_status: Literal["resolved", "unresolved"]
    candidate: list[TechnicalDiagnostic] = Field(default_factory=list)
    attempts: list[PreparationAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def final_outcome(self):
        resolved = any(a.status == "resolved" for a in self.attempts)
        if resolved != (self.preparation_status == "resolved"):
            raise ValueError("final preparation outcome contradicts method attempts")
        if any(d.scope == "preparation" for d in self.candidate):
            raise ValueError("candidate diagnostics cannot carry preparation outcomes")
        return self


class NumericSpan(Strict):
    span_id: str
    field_path: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    state: Literal[
        "cited", "derived", "uncited", "ambiguous", "unavailable", "identifier"
    ] = "uncited"
    claim_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    # Evidence bound to this one number, when the producer supplied it: the
    # source line(s) holding the value, or the calculation and its inputs.
    citations: list[Citation] = Field(default_factory=list)
    calculation: Calculation | None = None
    context: NumericContext | None = None
    prepared_evidence: list[PreparedEvidence] = Field(default_factory=list)
    diagnostics: NumericDiagnostics | None = None


class ReviewWorkload(Strict):
    answer_index: int = Field(ge=1)
    assigned_answers: int = Field(ge=1)
    task_counts: dict[str, int] = Field(default_factory=dict)
    assignment_reason: str = ""
    expansion_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def counts(self):
        if self.answer_index > self.assigned_answers or any(v < 0 for v in self.task_counts.values()):
            raise ValueError("invalid assigned workload counts")
        return self


class ReferenceItem(Strict):
    reference_id: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    calculation: Calculation | None = None


class FormField(Strict):
    field_id: str
    label: str
    kind: Literal["choice", "text", "boolean"] = "choice"
    options: list[str] = Field(default_factory=list)
    required: bool = True
    require_true: bool = False
    subject_id: str | None = None
    evidence_required: bool = False
    note_required_unless: list[str] = Field(default_factory=list)
    # Plain-language guidance shown with the control: what the question asks and
    # what each option means. Display only; never part of an answer's validity.
    help: str = ""
    option_help: dict[str, str] = Field(default_factory=dict)


class ReviewBundle(Strict):
    schema_version: Literal["review-bundle/v1"] = "review-bundle/v1"
    bundle_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    task_kind: Literal["independent", "adjudication", "reference", "finding"] = (
        "independent"
    )
    document_hashes: dict[str, str]
    # What the reviewer is asked to do in this task, in plain language.
    instructions: str = ""
    workload: ReviewWorkload | None = None
    fields: list[ReportField]
    sources: list[Source]
    spans: list[NumericSpan]
    claims: list[Claim]
    references: list[ReferenceItem] = Field(default_factory=list)
    form: list[FormField]
    # Adjudication disclosures are caller-authorized and rejected for independent tasks.
    disclosures: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete(self):
        from .evidence import numeric_spans, validate_citation

        groups = [
            (self.fields, "path"),
            (self.sources, "source_id"),
            (self.claims, "claim_id"),
            (self.spans, "span_id"),
            (self.references, "reference_id"),
            (self.form, "field_id"),
        ]
        for items, key in groups:
            if len({getattr(i, key) for i in items}) != len(items):
                raise ValueError(f"duplicate {key}")
        sources = {s.source_id: s for s in self.sources}
        claims = {c.claim_id: c for c in self.claims}
        expected = [
            (f.path, n.start, n.end, n.text)
            for f in self.fields
            for n in numeric_spans(f.path, f.text)
        ]
        actual = [(n.field_path, n.start, n.end, n.text) for n in self.spans]
        if sorted(expected) != sorted(actual):
            raise ValueError("numeric inventory incomplete or stale")
        for item in [*self.spans, *self.fields]:
            if set(item.claim_ids) - claims.keys():
                raise ValueError("unknown claim link")
        for n in self.spans:
            if n.state in ("cited", "derived") and not n.claim_ids:
                raise ValueError("mapped span needs claims")
            if n.diagnostics and bool(n.prepared_evidence) != (n.diagnostics.preparation_status == "resolved"):
                raise ValueError("prepared evidence contradicts diagnostic outcome")
            if n.state == "identifier" and (n.citations or n.calculation or n.prepared_evidence or n.diagnostics):
                raise ValueError("identifier spans carry no evidence")
        context = {f.path for f in self.fields if f.role == "context"}
        for n in self.spans:
            if n.field_path in context and (
                n.claim_ids or n.citations or n.calculation or n.prepared_evidence or n.context or n.diagnostics or n.state != "identifier"
            ):
                raise ValueError("context numbers are not assertions")
        def validate_calc(calc, depth=0):
            if depth > 12:
                raise ValueError("intermediate calculation nesting too deep")
            names = [o.name for o in calc.operands]
            if len(set(names)) != len(names):
                raise ValueError("duplicate calculation operand")
            for operand in calc.operands:
                if operand.citation:
                    validate_citation(operand.citation, sources)
                if operand.calculation:
                    validate_calc(operand.calculation, depth + 1)

        for n in self.spans:
            if n.diagnostics:
                for d in [*n.diagnostics.candidate, *(d for a in n.diagnostics.attempts for d in a.diagnostics)]:
                    if d.citation:
                        validate_citation(d.citation, sources)
        prepared = [p for n in self.spans for p in n.prepared_evidence]
        for c in [*self.claims, *self.references, *self.spans, *prepared]:
            for citation in c.citations:
                validate_citation(citation, sources)
            if c.calculation:
                validate_calc(c.calculation)
        if self.task_kind == "independent" and self.disclosures:
            raise ValueError("independent tasks cannot disclose labels or identity")
        for f in self.form:
            if set(f.option_help) - set(f.options):
                raise ValueError("help for an unknown option")
            if f.kind == "choice" and (
                not f.options or len(set(f.options)) != len(f.options)
            ):
                raise ValueError("choice needs unique options")
        return self

    @property
    def bundle_hash(self):
        return digest(self.model_dump(mode="json"))


class Selection(Strict):
    source_id: str
    source_hash: str
    start_line: int
    end_line: int
    excerpt: str
    subject_id: str | None = None


class Judgment(Strict):
    value: str | bool
    note: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    selections: list[Selection] = Field(default_factory=list)


class Defect(Strict):
    defect_id: str
    category: str
    material: bool | None
    claim_ids: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    evidence_note: str = ""
    selections: list[Selection] = Field(default_factory=list)


class ReviewSubmission(Strict):
    schema_version: Literal["review-submission/v1"] = "review-submission/v1"
    bundle_id: str
    bundle_hash: str
    revision: int = Field(ge=1)
    assessor: str = Field(min_length=1)
    started_at: str
    submitted_at: str
    active_seconds: float = Field(ge=0)
    session_seconds: float = Field(ge=0)
    judgments: dict[str, Judgment]
    defects: list[Defect]
    complete: Literal[True]
    amendment_reason: str = ""
    provenance: dict[str, str]


def validate_bundle(data) -> ReviewBundle:
    return ReviewBundle.model_validate(data)


def blind_terms(terms):
    """Normalize caller-declared identity/label markers that must never be shown."""
    cleaned = sorted({str(t).strip().casefold() for t in terms if str(t).strip()})
    if any(len(t) < 3 for t in cleaned):
        raise ValueError("blinding markers need at least 3 characters to be meaningful")
    return tuple(cleaned)


def blind_violations(payload, terms):
    """Markers present anywhere in a JSON-serializable payload (case-insensitive)."""
    if not terms:
        return []
    text = json.dumps(payload, ensure_ascii=False).casefold()
    return [t for t in terms if t in text]
