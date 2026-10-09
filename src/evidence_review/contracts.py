"""Versioned neutral review contracts; no consumer-specific verdict policy."""

from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationInfo, model_validator, model_serializer


PACKAGE_VERSION = "0.6.3"


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


# Validation-context flag for replaying an already sealed record (export, hooks):
# derived fields are proven against their stored values instead of overwritten.
SEALED = "evidence_review.sealed"


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
    # What the value measures (e.g. "operating margin"). Display context only.
    metric: str = ""

    @model_validator(mode="after")
    def provenance_kind(self):
        if self.kind == "constant" and (self.citation or self.calculation):
            raise ValueError("mathematical constants carry no source evidence")
        if self.kind == "derived" and (not self.calculation or self.citation):
            raise ValueError("derived input needs its calculation, not a direct citation")
        if self.kind == "source" and self.calculation:
            raise ValueError("source input cannot carry an intermediate calculation")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        # Optional context extension must not alter hashes of already sealed bundles;
        # a supplied metric is serialized and hashed like every other field.
        if not self.metric:
            data.pop("metric", None)
        return data


class Calculation(Strict):
    formula: str
    operands: list[Operand]
    result: str
    unit: str = ""
    tolerance: str = "0"
    conversions: list[str] = Field(default_factory=list)
    recomputation: dict = Field(default_factory=dict)
    # The author's prose tolerance (e.g. "0.1 percentage point"). Display only:
    # never parsed, never used in recomputation; ``tolerance`` alone is numeric.
    declared_tolerance: str = ""

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        if not self.declared_tolerance:
            data.pop("declared_tolerance", None)
        return data

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
    # The author's own declared support status and reason (e.g. "inference: derived
    # from the margin trend"). Display only: never a citation, never support, and it
    # never checks a row or changes any verification status.
    support_declaration: str = ""

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        if not self.support_declaration:
            data.pop("support_declaration", None)
        return data


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
    # Independently located inputs, even when the whole calculation is unresolved.
    prepared_inputs: list[Operand] = Field(default_factory=list)


class ReviewWorkload(Strict):
    answer_index: int = Field(ge=1)
    assigned_answers: int = Field(ge=1)
    task_counts: dict[str, int] = Field(default_factory=dict)
    assignment_reason: str = ""
    expansion_conditions: list[str] = Field(default_factory=list)
    # Caller-defined generic noun for one unit of this workload ("Check" shows
    # "Check 1 of 3"). Display only; empty keeps the package's Answer/Task label.
    task_noun: str = Field(default="", pattern=r"^(?:[A-Za-z](?:[A-Za-z -]{0,38}[A-Za-z])?)?$")

    @model_validator(mode="after")
    def counts(self):
        if self.answer_index > self.assigned_answers or any(v < 0 for v in self.task_counts.values()):
            raise ValueError("invalid assigned workload counts")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        # Optional display extension must not alter hashes of already sealed bundles.
        if not self.task_noun:
            data.pop("task_noun", None)
        return data


class ReferenceItem(Strict):
    reference_id: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    calculation: Calculation | None = None


class AtomBinding(Strict):
    """One caller-declared non-numeric factual proposition inside an answer field.

    Quantities keep their legacy ``numeric_span_id`` binding; this binds a check
    control to exactly one fact occurrence, identified by its exact offsets."""
    atom_id: str = Field(pattern=r"^atom:[0-9a-f]{24}$")
    field_path: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


ATOM_CONTRACT = "atom/v1"


def atom_identity(bundle_id, document_hashes, field_path, field_text, start, end, kind):
    """Deterministic occurrence identity: the same words twice are two atoms."""
    return "atom:" + digest({
        "contract": ATOM_CONTRACT, "bundle_id": bundle_id, "documents": digest(document_hashes),
        "field_path": field_path, "field_sha256": digest(field_text), "start": start, "end": end, "kind": kind,
    })[:24]


class FormField(Strict):
    field_id: str
    label: str
    kind: Literal["choice", "text", "boolean"] = "choice"
    options: list[str] = Field(default_factory=list)
    required: bool = True
    require_true: bool = False
    subject_id: str | None = None
    numeric_span_id: str | None = None
    numeric_verification_values: list[str] = Field(default_factory=list)
    evidence_required: bool = False
    note_required_unless: list[str] = Field(default_factory=list)
    # Plain-language guidance shown with the control: what the question asks and
    # what each option means. Display only; never part of an answer's validity.
    help: str = ""
    option_help: dict[str, str] = Field(default_factory=dict)
    # Explicit check of one declared fact atom (atomic source-check forms only).
    atom: AtomBinding | None = None


    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        # Optional extension must not alter hashes of already sealed bundles.
        if self.numeric_span_id is None:
            data.pop("numeric_span_id", None)
        if self.atom is None:
            data.pop("atom", None)
        if not self.numeric_verification_values:
            data.pop("numeric_verification_values", None)
        return data


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
        spans_by_id = {s.span_id: s for s in self.spans}
        quantity_bindings = set()
        atom_bindings = set()
        for field in self.form:
            if field.numeric_span_id:
                span = spans_by_id.get(field.numeric_span_id)
                if (span is None or span.state == "identifier" or field.kind != "boolean"
                        or field.required or field.require_true or not field.subject_id
                        or field.subject_id not in span.claim_ids):
                    raise ValueError("invalid quantity verification binding")
                if field.numeric_span_id in quantity_bindings:
                    raise ValueError("duplicate quantity verification binding")
                quantity_bindings.add(field.numeric_span_id)
            if field.atom:
                a = field.atom
                answer = {f.path: f for f in self.fields if f.role == "answer"}.get(a.field_path)
                subjects = {c.claim_id for c in self.claims} | {r.reference_id for r in self.references}
                if (field.numeric_span_id or field.kind != "boolean" or field.required or field.require_true
                        or not field.subject_id or field.subject_id not in subjects or answer is None
                        or a.end > len(answer.text) or a.start >= a.end or answer.text[a.start:a.end] != a.text
                        or a.atom_id != atom_identity(self.bundle_id, self.document_hashes, a.field_path,
                                                      answer.text, a.start, a.end, "fact")):
                    raise ValueError("invalid atom verification binding")
                if a.atom_id in atom_bindings:
                    raise ValueError("duplicate atom verification binding")
                atom_bindings.add(a.atom_id)
            if field.numeric_verification_values:
                if (field.kind != "choice" or not field.subject_id
                        or set(field.numeric_verification_values) - set(field.options)
                        or not any((q.numeric_span_id or q.atom) and q.subject_id == field.subject_id for q in self.form)):
                    raise ValueError("invalid quantity verification verdict")
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
            if n.state == "identifier" and (n.citations or n.calculation or n.prepared_evidence or n.prepared_inputs or n.diagnostics):
                raise ValueError("identifier spans carry no evidence")
        context = {f.path for f in self.fields if f.role == "context"}
        for n in self.spans:
            if n.field_path in context and (
                n.claim_ids or n.citations or n.calculation or n.prepared_evidence or n.prepared_inputs or n.context or n.diagnostics or n.state != "identifier"
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
        from .evidence import arithmetic

        for n in self.spans:
            original = {o.name: o for o in n.calculation.operands} if n.calculation else {}
            if len({o.name for o in n.prepared_inputs}) != len(n.prepared_inputs):
                raise ValueError("duplicate prepared input")
            for o in n.prepared_inputs:
                candidate = original.get(o.name)
                try:
                    with arithmetic():
                        equal_value = candidate is not None and Decimal(o.value).is_finite() and Decimal(candidate.value).is_finite() and Decimal(o.value) == Decimal(candidate.value)
                except InvalidOperation:
                    equal_value = False
                if not equal_value or any(getattr(o, k) != getattr(candidate, k) for k in ("unit", "period")):
                    raise ValueError("prepared input does not match candidate identity, value, period and unit")
                if o.citation:
                    validate_citation(o.citation, sources)
                if o.calculation:
                    validate_calc(o.calculation)
                from .evidence import operand_evidence, calculation_citations
                source_leaves = ([o.citation] if o.citation else []) + (list(calculation_citations(o.calculation)) if o.calculation else [])
                if o.kind == "constant" or not source_leaves or any(c.status != "located" for c in source_leaves):
                    raise ValueError("prepared input requires actual located source leaves")
                if operand_evidence([o], o.unit, [])["missing"]:
                    raise ValueError("prepared input must have located source evidence")
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
        payload = self.model_dump(mode="json")
        # Absent optional diagnostics preserve pre-diagnostics durable identities.
        # Supplied diagnostics remain hashed; all other legacy fields stay intact.
        for span in payload["spans"]:
            if not span["prepared_inputs"]:
                del span["prepared_inputs"]
            if span["diagnostics"] is None:
                del span["diagnostics"]
        return digest(payload)


class Selection(Strict):
    source_id: str
    source_hash: str
    start_line: int
    end_line: int
    excerpt: str
    subject_id: str | None = None


ANSWER_ANNOTATION_CONTRACT = "answer-annotation/v1"
# Offsets index the exact answer text as a sequence of Unicode code points (Python
# ``str`` indexing; ``Array.from(text)`` in the browser), never UTF-16 units or
# bytes. A combining mark is its own code point; an astral character is one.
OFFSET_UNIT = "code_point"
DISPOSITIONS = ("supported", "defective", "cannot_verify", "incomplete")
# Materiality applies only where the reviewer reports a problem.
MATERIAL_DISPOSITIONS = ("defective", "incomplete")


def documents_digest(document_hashes) -> str:
    """One digest binding an answer range to the bundle's declared report/document hashes."""
    return digest(document_hashes)


class AnswerRange(Strict):
    """Exact reviewer-selected text in one original answer field (answer-annotation/v1).

    ``start``/``end`` are half-open code-point offsets into the field's exact text;
    the field and document digests make a range recorded against other text stale."""
    contract: Literal["answer-annotation/v1"] = ANSWER_ANNOTATION_CONTRACT
    field_path: str = Field(min_length=1)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    text: str = Field(min_length=1)
    offset_unit: Literal["code_point"] = OFFSET_UNIT
    field_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    documents_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnswerAnnotation(Strict):
    """A reviewer's finding about one selected answer passage (answer-annotation/v1).

    Annotations record only what the reviewer chose to annotate; they never claim
    the answer was reviewed exhaustively, and an unannotated passage is not
    thereby supported. An omission has no answer range and is recorded as a
    ``Defect`` without ``answer_ranges``. ``annotation_id`` is the stable handle
    other records (e.g. a reviewer worksheet) use to link to this annotation."""
    contract: Literal["answer-annotation/v1"] = ANSWER_ANNOTATION_CONTRACT
    annotation_id: str = Field(pattern=r"^ann:[A-Za-z0-9_-]{1,64}$")
    answer_range: AnswerRange
    # None is an undecided draft; submission requires an explicit disposition.
    disposition: Literal["supported", "defective", "cannot_verify", "incomplete"] | None = None
    reason: str = ""
    material: bool | None = None
    selections: list[Selection] = Field(default_factory=list)

    @model_validator(mode="after")
    def materiality_where_applicable(self):
        if self.material is not None and self.disposition not in MATERIAL_DISPOSITIONS:
            raise ValueError("materiality applies only to defective or incomplete annotations")
        return self


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
    # Exact answer passages the defect concerns (answer-annotation/v1). Empty means
    # an omission or whole-answer defect, recorded without any answer range.
    answer_ranges: list[AnswerRange] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        # Optional extension must not alter the bytes of already exported submissions.
        if not self.answer_ranges:
            data.pop("answer_ranges", None)
        return data


WORKSHEET_CONTRACT = "reviewer-calculation-worksheet/v1"
SUBJECT_KINDS = ("claim", "reference", "span", "defect", "annotation")


class SubjectRef(Strict):
    """What a reviewer record is about: a bundle claim, reference or numeric span,
    a defect in the same answers, or an answer annotation (by ``annotation_id``)."""
    kind: Literal["claim", "reference", "span", "defect", "annotation"]
    id: str = Field(min_length=1, max_length=200)


class WorksheetOperand(Strict):
    """One reviewer-entered input. ``selected`` inputs carry their source passages;
    ``unavailable`` inputs carry no value and say why (retained, never filled in)."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    value: str = Field(default="", max_length=200)
    unit: str = ""
    period: str = ""
    entity: str = ""
    metric: str = ""
    availability: Literal["selected", "unavailable"] = "selected"
    unavailable_reason: str = ""
    selections: list[Selection] = Field(default_factory=list)

    @model_validator(mode="after")
    def explicit_state(self):
        import keyword

        if keyword.iskeyword(self.name):
            raise ValueError("operand name is a reserved word")
        # Non-finite values raise; unparseable text is retained as an input error.
        finite_decimal(self.value, "operand " + self.name)
        if self.availability == "unavailable" and (self.value or self.selections):
            raise ValueError("an unavailable operand carries no value or source selection")
        return self


def finite_decimal(text, what):
    """True for a finite decimal, None for empty, False for unparseable text.

    Non-finite values (NaN, Infinity) are refused outright."""
    from .evidence import arithmetic

    if text == "":
        return None
    try:
        with arithmetic():  # parse errors raise whatever traps the caller cleared
            value = Decimal(text.strip())
    except InvalidOperation:
        return False
    if not value.is_finite():
        raise ValueError(f"{what}: non-finite value refused")
    return True


class CalculationWorksheet(Strict):
    """A reviewer-authored calculation, kept apart from candidate and prepared ones.

    It never replaces a bundle formula. ``computation`` is derived on every
    validation with the package's bounded Decimal evaluator under its pinned
    arithmetic context, so a client cannot supply its own result; arithmetic
    never sets ``reviewer_support``. Replaying a sealed record (validation
    context ``SEALED``) proves the stored computation instead: it is kept byte for
    byte when the replay agrees and refused, naming the worksheet, when not."""
    worksheet_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    contract: Literal["reviewer-calculation-worksheet/v1"] = "reviewer-calculation-worksheet/v1"
    authorship: Literal["reviewer"] = "reviewer"
    subject: SubjectRef | None = None
    formula: str = Field(default="", max_length=1000)
    operands: list[WorksheetOperand] = Field(default_factory=list, max_length=50)
    reported_value: str = Field(default="", max_length=200)
    unit: str = ""
    tolerance: str = Field(default="0", max_length=200)
    rationale: str = ""
    # The reviewer's own support judgment for the subject; never inferred from arithmetic.
    reviewer_support: Literal["unknown", "supported", "unsupported"] = "unknown"
    computation: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def recompute(self, info: ValidationInfo):
        from .evidence import formula_names, sealed_computation, worksheet_computation

        names = [o.name for o in self.operands]
        if len(set(names)) != len(names):
            raise ValueError("duplicate worksheet operand")
        for text, what in ((self.reported_value, "reported value"), (self.tolerance, "tolerance")):
            finite_decimal(text, what)
        if self.formula.strip():
            try:
                formula_names(self.formula)  # unsafe constructs raise and are never stored
            except SyntaxError:
                pass  # an incomplete expression is retained as an input error
        if (info.context or {}).get(SEALED):
            self.computation = sealed_computation(self, self.computation)
        else:
            self.computation = worksheet_computation(self)
        return self


def sealed_submission(data) -> "ReviewSubmission":
    """Validate a stored submission without rewriting any sealed derived field."""
    return ReviewSubmission.model_validate(data, context={SEALED: True})


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
    # Reviewer-authored calculations (optional); omitted when empty so earlier
    # submissions serialize and hash exactly as they did.
    worksheets: list[CalculationWorksheet] = Field(default_factory=list)
    complete: Literal[True]
    amendment_reason: str = ""
    provenance: dict[str, str]
    # Reviewer-selected answer annotations (answer-annotation/v1).
    annotations: list[AnswerAnnotation] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        # Absent annotations keep the exact canonical bytes of earlier submissions.
        if not self.annotations:
            data.pop("annotations", None)
        if not self.worksheets:
            data.pop("worksheets", None)
        return data


def answer_range(bundle, field_path, start, end) -> AnswerRange:
    """Build the exact range for code-point offsets ``[start, end)`` of an answer field."""
    field = next((f for f in bundle.fields if f.path == field_path and f.role == "answer"), None)
    if field is None:
        raise ValueError("answer range names no answer field")
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(field.text)):
        raise ValueError("answer range out of bounds")
    return validate_answer_range(bundle, AnswerRange(
        field_path=field_path, start=start, end=end, text=field.text[start:end],
        field_sha256=digest(field.text), documents_sha256=documents_digest(bundle.document_hashes)))


def validate_answer_range(bundle, selected) -> AnswerRange:
    """Refuse a range that is unknown, stale, out of bounds or not the exact text."""
    selected = AnswerRange.model_validate(selected.model_dump() if hasattr(selected, "model_dump") else selected)
    field = next((f for f in bundle.fields if f.path == selected.field_path), None)
    if field is None or field.role != "answer":
        raise ValueError("answer range names no answer field")
    if (selected.field_sha256 != digest(field.text)
            or selected.documents_sha256 != documents_digest(bundle.document_hashes)):
        raise ValueError("answer range stale: answer text or documents changed")
    if not 0 <= selected.start < selected.end <= len(field.text):
        raise ValueError("answer range out of bounds")
    if field.text[selected.start:selected.end] != selected.text:
        raise ValueError("answer range text mismatch")
    return selected


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
