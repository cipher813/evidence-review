"""Opt-in atomic evidence and rendered-source sidecars (atom-evidence/v1, source-render-manifest/v1).

Both sidecars are hash-bound to one unchanged ReviewBundle: they add navigation
and an explicit per-atom inventory without altering bundle or submission
identity. The package validates structure, occurrence offsets, hashes and target
references; it never decides whether a source supports an atom. Atomization of
compound prose is caller-owned and declared, never inferred here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_serializer, model_validator

from .contracts import ATOM_CONTRACT, Citation, Strict, atom_identity, digest
from .evidence import calculation_citations, validate_citation

ATOM_EVIDENCE_SCHEMA = "atom-evidence/v1"
RENDER_MANIFEST_SCHEMA = "source-render-manifest/v1"
# Contracts this package version understands; anything else is refused, never ignored.
SUPPORTED_CONTRACTS = (ATOM_EVIDENCE_SCHEMA, RENDER_MANIFEST_SCHEMA)
SAFE_ID = r"^[A-Za-z0-9_.:-]{1,160}$"
TEXT_HASH_ALGORITHM = "evidence-review/text-sha256-utf8"


class RenderedSourceTarget(Strict):
    """One cited location mapped from frozen text into a rendered derivative."""
    target_id: str = Field(pattern=r"^tgt:[0-9a-f]{24}$")
    source_id: str = Field(pattern=SAFE_ID)
    frozen_sha256: str
    raw_sha256: str | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    excerpt: str = ""
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    dom_targets: list[str] = Field(default_factory=list)
    candidates: list[list[str]] = Field(default_factory=list)
    status: Literal["exact", "page_only", "ambiguous", "unavailable"]
    reason: str = ""

    @model_validator(mode="after")
    def located(self):
        if self.end_line < self.start_line:
            raise ValueError("target line range inverted")
        if self.status == "exact" and not self.dom_targets:
            raise ValueError("exact target needs a rendered node")
        if self.status == "ambiguous" and len(self.candidates) < 2:
            raise ValueError("ambiguous target needs at least two candidates")
        if self.status in ("page_only", "ambiguous", "unavailable") and not self.reason:
            raise ValueError("limited target needs a reason")
        if self.status == "page_only" and self.page is None:
            raise ValueError("page-only target needs its page")
        if self.bbox is not None and (len(self.bbox) != 4 or self.page is None):
            raise ValueError("bounding box needs four coordinates and a page")
        return self


class SourceRenderManifest(Strict):
    schema_version: Literal["source-render-manifest/v1"] = RENDER_MANIFEST_SCHEMA
    source_id: str = Field(pattern=SAFE_ID)
    bundle_hash: str
    frozen_sha256: str
    frozen_hash_algorithm: Literal["evidence-review/text-sha256-utf8"] = TEXT_HASH_ALGORITHM
    raw_sha256: str | None = None
    raw_media_type: str | None = None
    # What the reviewer sees: a faithful presentation of frozen original bytes,
    # a readable rendering of the normalized frozen text, or a stated limitation.
    render_mode: Literal["faithful_markdown", "faithful_html", "pdf_text_layer", "normalized_snapshot",
                         "unsupported"]
    lineage: Literal["raw_matches_frozen", "raw_differs_from_frozen", "raw_derived_text", "raw_unavailable"]
    fidelity_note: str = Field(min_length=1)
    derivative_sha256: str
    mapping_sha256: str
    renderer: dict[str, str]
    transforms: list[str] = Field(default_factory=list)
    targets: list[RenderedSourceTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        ids = [t.target_id for t in self.targets]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate render target")
        if any(t.source_id != self.source_id or t.frozen_sha256 != self.frozen_sha256 for t in self.targets):
            raise ValueError("render target belongs to another source")
        if (self.raw_sha256 is None) != (self.lineage == "raw_unavailable"):
            raise ValueError("raw lineage and raw hash disagree")
        if self.mapping_sha256 != digest([t.model_dump(mode="json") for t in self.targets]):
            raise ValueError("render mapping hash mismatch")
        return self


class AtomEvidenceItem(Strict):
    atom_id: str = Field(pattern=r"^atom:[0-9a-f]{24}$")
    bundle_hash: str
    field_path: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)
    kind: Literal["fact", "quantity"]
    claim_ids: list[str] = Field(default_factory=list)
    numeric_span_id: str | None = None
    form_field_id: str | None = None
    evidence_state: Literal["located", "derived", "ambiguous", "unavailable", "unsupported"]
    reason: str = ""
    citation_target_ids: list[str] = Field(default_factory=list)
    candidate_target_ids: list[str] = Field(default_factory=list)
    # The calculation is read from the bound span or claim, never restated here.
    calculation_ref: str | None = None
    # Independently prepared evidence for this occurrence, ``prepared:<span id>:<index>``
    # into that span's ``prepared_evidence``. Shown beside the original candidate
    # evidence, never instead of it, and never a support judgment.
    prepared_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "quantity" and not self.numeric_span_id:
            raise ValueError("quantity atom needs its numeric span")
        if self.kind == "fact" and self.numeric_span_id:
            raise ValueError("fact atom cannot borrow a numeric span")
        if self.evidence_state == "located" and not self.citation_target_ids:
            raise ValueError("located atom needs a citation target")
        if self.evidence_state == "derived" and not self.calculation_ref:
            raise ValueError("derived atom needs its calculation reference")
        if self.evidence_state == "ambiguous" and len(self.candidate_target_ids) < 2:
            raise ValueError("ambiguous atom needs at least two candidate targets")
        if self.evidence_state in ("ambiguous", "unavailable", "unsupported") and not self.reason:
            raise ValueError("unresolved atom needs a reason")
        if self.evidence_state != "ambiguous" and self.candidate_target_ids:
            raise ValueError("only ambiguous atoms carry candidates")
        if len(set(self.prepared_refs)) != len(self.prepared_refs):
            raise ValueError("duplicate prepared evidence reference")
        return self

    @model_serializer(mode="wrap")
    def omit_empty_extension(self, handler):
        data = handler(self)
        # Optional extension: manifests without preparation serialize as before.
        if not self.prepared_refs:
            data.pop("prepared_refs", None)
        return data


class Atomization(Strict):
    """How the caller split prose into atoms; reviewable, never inferred here."""
    method: str = Field(min_length=1)
    version: str = Field(min_length=1)
    record_sha256: str = ""
    # complete: every asserted number in scope is an atom. assigned: only the
    # assigned atoms are rows; every other asserted number is listed as context.
    coverage: Literal["complete", "assigned"] = "complete"
    scope_fields: list[str] = Field(default_factory=list)
    context_span_ids: list[str] = Field(default_factory=list)


class AtomCitation(Strict):
    """A caller-declared located citation for a fact atom, validated against frozen text.

    ``atom_ids`` attributes the citation to the exact fact occurrences it was
    declared for; a fact atom may navigate to a declared citation only when it
    is named here (or the citation is one of its linked claims' own)."""
    target_id: str = Field(pattern=r"^tgt:[0-9a-f]{24}$")
    citation: Citation
    atom_ids: list[str] = Field(default_factory=list)


class AtomEvidenceManifest(Strict):
    schema_version: Literal["atom-evidence/v1"] = ATOM_EVIDENCE_SCHEMA
    atom_contract: Literal["atom/v1"] = ATOM_CONTRACT
    bundle_id: str
    bundle_hash: str
    atomization: Atomization
    items: list[AtomEvidenceItem]
    # Fact atoms may cite lines the bundle itself never cited; each is listed here.
    citations: list[AtomCitation] = Field(default_factory=list)
    provenance: Literal["contemporaneous", "retrospective"] = "contemporaneous"


def target_identity(source_id, frozen_sha256, start_line, end_line, excerpt):
    return "tgt:" + digest({"source_id": source_id, "frozen_sha256": frozen_sha256, "start_line": start_line,
                            "end_line": end_line, "excerpt": " ".join(excerpt.split())})[:24]


def citation_target_id(bundle, citation):
    """Target id for a located citation of this bundle (None when unlocated)."""
    if citation is None or citation.status != "located":
        return None
    source = next((s for s in bundle.sources if s.source_id == citation.source_id), None)
    if source is None:
        return None
    return target_identity(source.source_id, source.sha256, citation.start_line, citation.end_line, citation.excerpt)


def bundle_citations(bundle):
    """Every located citation the bundle can navigate to, in deterministic order."""
    seen, out = set(), []

    def add(c):
        if c is not None and c.status == "located":
            key = (c.source_id, c.start_line, c.end_line, " ".join(c.excerpt.split()))
            if key not in seen:
                seen.add(key)
                out.append(c)

    def calc(c):
        for leaf in calculation_citations(c):
            add(leaf)

    for item in [*bundle.claims, *bundle.references, *bundle.spans]:
        for c in item.citations:
            add(c)
        if item.calculation:
            calc(item.calculation)
    for span in bundle.spans:
        for p in span.prepared_evidence:
            for c in p.citations:
                add(c)
            if p.calculation:
                calc(p.calculation)
        for o in span.prepared_inputs:
            add(o.citation)
            if o.calculation:
                calc(o.calculation)
    return out


def _span_targets(bundle, span):
    if span.calculation:
        return [], "derived", f"span:{span.span_id}"
    cites = [c for c in span.citations if c.status == "located"]
    if span.state == "ambiguous" or len(cites) > 1:
        return [citation_target_id(bundle, c) for c in cites], "ambiguous", None
    if len(cites) == 1:
        return [citation_target_id(bundle, cites[0])], "located", None
    return [], "unavailable", None


def prepared_ref(span, index):
    return f"prepared:{span.span_id}:{index}"


def _prepared_for(bundle, ref):
    """The (span, PreparedEvidence) a ``prepared:<span id>:<index>`` reference names, or None."""
    kind, _, rest = (ref or "").partition(":")
    span_id, _, index = rest.rpartition(":")
    if kind != "prepared" or not index.isdigit() or str(int(index)) != index:
        return None
    span = next((s for s in bundle.spans if s.span_id == span_id), None)
    if span is None or int(index) >= len(span.prepared_evidence):
        return None
    return span, span.prepared_evidence[int(index)]


def build_atom_manifest(bundle, facts=(), *, method="evidence-review/numeric-inventory", version="1",
                        record_sha256="", coverage="complete", scope_fields=None, assigned_span_ids=None,
                        provenance="contemporaneous"):
    """Quantity atoms from the bundle's own numeric inventory plus caller facts.

    ``facts`` are dicts ``{field_path, start, end, claim_ids, evidence_state,
    reason, citations, calculation_ref, prepared_refs}`` declared by the caller's atomization;
    ``citations`` are bundle Citation objects. With ``coverage="assigned"``
    only ``assigned_span_ids`` become quantity atoms and every other asserted
    number in scope is recorded as context, so nothing is silently dropped.
    """
    fields = {f.path: f for f in bundle.fields if f.role == "answer"}
    declared = {}
    scope = list(scope_fields if scope_fields is not None else fields)
    asserted = [s for s in bundle.spans if s.field_path in scope and s.state != "identifier"]
    assigned = set(assigned_span_ids or ()) if coverage == "assigned" else {s.span_id for s in asserted}
    checks = {f.numeric_span_id: f.field_id for f in bundle.form if f.numeric_span_id}
    fact_checks = {f.atom.atom_id: f.field_id for f in bundle.form if f.atom}
    items = []
    for span in asserted:
        if span.span_id not in assigned:
            continue
        field = fields[span.field_path]
        targets, state, calc = _span_targets(bundle, span)
        items.append(AtomEvidenceItem(
            atom_id=atom_identity(bundle.bundle_id, bundle.document_hashes, span.field_path, field.text,
                                  span.start, span.end, "quantity"),
            bundle_hash=bundle.bundle_hash, field_path=span.field_path, start=span.start, end=span.end,
            text=span.text, kind="quantity", claim_ids=list(span.claim_ids), numeric_span_id=span.span_id,
            form_field_id=checks.get(span.span_id), evidence_state=state,
            reason=span.reason or ({"ambiguous": "Several candidate sources; no unique match",
                                    "unavailable": "No source located for this number"}.get(state, "")),
            citation_target_ids=targets if state == "located" else [],
            candidate_target_ids=targets if state == "ambiguous" else [], calculation_ref=calc,
            prepared_refs=[prepared_ref(span, i) for i in range(len(span.prepared_evidence))]))
    for fact in facts:
        field = fields[fact["field_path"]]
        start, end = fact["start"], fact["end"]
        atom_id = atom_identity(bundle.bundle_id, bundle.document_hashes, fact["field_path"], field.text, start, end, "fact")
        cites = [c for c in fact.get("citations", []) if c.status == "located"]
        state = fact.get("evidence_state") or ("located" if len(cites) == 1 else "ambiguous" if cites else "unavailable")
        targets = [citation_target_id(bundle, c) for c in cites]
        for t, c in zip(targets, cites):
            entry = declared.setdefault(t, AtomCitation(target_id=t, citation=c))
            if atom_id not in entry.atom_ids:
                entry.atom_ids.append(atom_id)
        items.append(AtomEvidenceItem(
            atom_id=atom_id, bundle_hash=bundle.bundle_hash, field_path=fact["field_path"], start=start, end=end,
            text=field.text[start:end], kind="fact", claim_ids=list(fact.get("claim_ids", [])),
            form_field_id=fact_checks.get(atom_id), evidence_state=state, reason=fact.get("reason", ""),
            citation_target_ids=targets if state == "located" else [],
            candidate_target_ids=targets if state == "ambiguous" else [],
            calculation_ref=fact.get("calculation_ref"), prepared_refs=list(fact.get("prepared_refs", ()))))
    context = [s.span_id for s in asserted if s.span_id not in assigned]
    return AtomEvidenceManifest(
        bundle_id=bundle.bundle_id, bundle_hash=bundle.bundle_hash, provenance=provenance,
        atomization=Atomization(method=method, version=version, record_sha256=record_sha256, coverage=coverage,
                                scope_fields=scope, context_span_ids=context),
        items=items, citations=list(declared.values()))


def validate_render_manifest(bundle, payload):
    """A render manifest bound to this exact bundle and one of its sources."""
    manifest = SourceRenderManifest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
    source = next((s for s in bundle.sources if s.source_id == manifest.source_id), None)
    if source is None:
        raise ValueError("render manifest names a source outside this bundle")
    if manifest.bundle_hash != bundle.bundle_hash:
        raise ValueError("render manifest is bound to a different bundle")
    if manifest.frozen_sha256 != source.sha256:
        raise ValueError("render manifest frozen hash differs from the bundle source")
    lines = source.text.splitlines()
    for t in manifest.targets:
        if t.end_line > len(lines):
            raise ValueError("render target outside the frozen source")
        if t.target_id != target_identity(t.source_id, t.frozen_sha256, t.start_line, t.end_line, t.excerpt):
            raise ValueError("render target identity mismatch")
    return manifest


def _calculation_for(bundle, ref):
    kind, _, ident = (ref or "").partition(":")
    if kind == "span":
        span = next((s for s in bundle.spans if s.span_id == ident), None)
        return span.calculation if span else None
    if kind == "claim":
        claim = next((c for c in bundle.claims if c.claim_id == ident), None)
        return claim.calculation if claim else None
    return None


def _located_ids(bundle, citations):
    return [t for t in (citation_target_id(bundle, c) for c in citations) if t]


def atom_target_bindings(bundle, manifest, item):
    """Targets this one atom may navigate to, each labelled with where its binding comes from.

    A quantity is bound to its own span's citations or to the span's explicitly
    attributed ``prepared_evidence`` records, both occurrence-bound by the span
    id. A fact is bound to its linked claims' or references' own citations, or
    to a declared citation attributed to this exact atom. Nothing else in the
    bundle's target pool is this atom's evidence."""
    bound = {}
    if item.kind == "quantity":
        span = next((s for s in bundle.spans if s.span_id == item.numeric_span_id), None)
        if span is None:
            return bound
        for t in _located_ids(bundle, span.citations):
            bound.setdefault(t, "span_citation")
        for p in span.prepared_evidence:
            for t in _located_ids(bundle, p.citations):
                bound.setdefault(t, "prepared_evidence")
        return bound
    linked = set(item.claim_ids)
    for c in [*bundle.claims, *bundle.references]:
        if (getattr(c, "claim_id", None) or getattr(c, "reference_id", None)) in linked:
            for t in _located_ids(bundle, c.citations):
                bound.setdefault(t, "linked_statement_citation")
    for d in manifest.citations:
        if item.atom_id in d.atom_ids:
            bound.setdefault(d.target_id, "declared_atom_citation")
    return bound


def calculation_bound(bundle, item):
    """Whether this atom's calculation reference is its own occurrence's calculation."""
    kind, _, ident = (item.calculation_ref or "").partition(":")
    if item.kind == "quantity":
        return kind == "span" and ident == item.numeric_span_id
    if kind == "claim":
        return ident in item.claim_ids
    if kind == "span":
        span = next((s for s in bundle.spans if s.span_id == ident), None)
        # A fact may use the calculation of a number written inside its own words.
        return (span is not None and span.field_path == item.field_path and item.start <= span.start
                and span.end <= item.end)
    return False


def _validate_check_binding(item, form, checks):
    """Bidirectional row/control binding: a row names exactly the frozen check bound to its occurrence.

    The bundle's form is immutable; a sidecar may neither hide a check the form
    binds to this atom (omitted or replaced id) nor claim a control the form
    does not bind to it. A row with no bound check stays legitimately unassigned."""
    expected = checks.get(item.numeric_span_id if item.kind == "quantity" else item.atom_id)
    what = (f"quantity atom {item.atom_id} (number {item.text!r} at {item.field_path}:{item.start}-{item.end})"
            if item.kind == "quantity" else f"fact atom {item.atom_id} ({item.field_path}:{item.start}-{item.end})")
    if item.form_field_id is not None and item.form_field_id not in form:
        raise ValueError(f"{what} names check control {item.form_field_id!r}, which the frozen form does not have")
    if expected is None and item.form_field_id is not None:
        raise ValueError(f"{what} names check control {item.form_field_id!r}, which the frozen form does not "
                         "bind to this atom")
    if expected is not None and item.form_field_id is None:
        raise ValueError(f"{what} omits its frozen check control {expected!r}; "
                         "set form_field_id so the required control stays reachable")
    if expected is not None and item.form_field_id != expected:
        raise ValueError(f"{what} names check control {item.form_field_id!r}, but the frozen form binds "
                         f"{expected!r} to it")


def _validate_prepared_refs(bundle, item):
    """Prepared references are this occurrence's own preparation, and a number lists all of its own."""
    for ref in item.prepared_refs:
        found = _prepared_for(bundle, ref)
        if found is None:
            raise ValueError(f"atom references unknown prepared evidence {ref!r}")
        span = found[0]
        if item.kind == "quantity":
            own = span.span_id == item.numeric_span_id
        else:
            # A fact may show the preparation of a number written inside its own words.
            own = span.field_path == item.field_path and item.start <= span.start and span.end <= item.end
        if not own:
            raise ValueError(f"prepared evidence {ref!r} belongs to another occurrence, not this atom's own")
    if item.kind == "quantity":
        span = next((s for s in bundle.spans if s.span_id == item.numeric_span_id), None)
        expected = [prepared_ref(span, i) for i in range(len(span.prepared_evidence))] if span else []
        if span is not None and item.prepared_refs != expected:
            raise ValueError("quantity atom must list exactly its own span's prepared evidence, in order; "
                             "known prepared evidence is never hidden")


def validate_atom_evidence(bundle, payload, render_manifests=()):
    """Structural, occurrence and hash integrity of an atom inventory.

    Raises on malformed identity, offsets, coverage or references. Unresolved
    atoms are accepted and stay visible with their reasons."""
    manifest = AtomEvidenceManifest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
    if manifest.bundle_id != bundle.bundle_id or manifest.bundle_hash != bundle.bundle_hash:
        raise ValueError("atom manifest is bound to a different bundle")
    answer = {f.path: f for f in bundle.fields if f.role == "answer"}
    spans = {s.span_id: s for s in bundle.spans}
    form = {f.field_id: f for f in bundle.form}
    claims = {c.claim_id for c in bundle.claims} | {r.reference_id for r in bundle.references}
    targets = {t.target_id: t for m in render_manifests for t in m.targets}
    known_targets = set(targets) | {citation_target_id(bundle, c) for c in bundle_citations(bundle)}
    sources = {s.source_id: s for s in bundle.sources}
    for declared in manifest.citations:
        if declared.citation.status != "located":
            raise ValueError("declared atom citation must be located")
        validate_citation(declared.citation, sources)
        if declared.target_id != citation_target_id(bundle, declared.citation):
            raise ValueError("declared atom citation identity mismatch")
        known_targets.add(declared.target_id)
    scope = set(manifest.atomization.scope_fields)
    if scope - answer.keys():
        raise ValueError("atomization scope names a non-answer field")
    quantity_checks = {f.numeric_span_id: f.field_id for f in bundle.form if f.numeric_span_id}
    fact_checks = {f.atom.atom_id: f.field_id for f in bundle.form if f.atom}
    ids, quantity_spans, facts = set(), set(), {}
    for item in manifest.items:
        field = answer.get(item.field_path)
        if item.bundle_hash != bundle.bundle_hash:
            raise ValueError("atom bound to a different bundle")
        if field is None or item.field_path not in scope:
            raise ValueError("atom outside the declared answer scope")
        if item.end > len(field.text) or item.start >= item.end or field.text[item.start:item.end] != item.text:
            raise ValueError("atom offsets do not match the frozen answer text")
        expected = atom_identity(bundle.bundle_id, bundle.document_hashes, item.field_path, field.text,
                                 item.start, item.end, item.kind)
        if item.atom_id != expected:
            raise ValueError("atom identity mismatch")
        if item.atom_id in ids:
            raise ValueError("duplicate atom")
        ids.add(item.atom_id)
        if set(item.claim_ids) - claims:
            raise ValueError("atom links an unknown claim")
        for t in [*item.citation_target_ids, *item.candidate_target_ids]:
            if t not in known_targets:
                raise ValueError("atom references an unknown citation target")
        if (item.calculation_ref or "").startswith("prepared:"):
            raise ValueError("a prepared calculation never replaces an atom's original calculation_ref; "
                             "list it in prepared_refs")
        if item.calculation_ref and _calculation_for(bundle, item.calculation_ref) is None:
            raise ValueError("atom references an unknown calculation")
        if item.calculation_ref and not calculation_bound(bundle, item):
            raise ValueError("atom calculation is not its own occurrence's calculation")
        _validate_prepared_refs(bundle, item)
        _validate_check_binding(item, form, quantity_checks if item.kind == "quantity" else fact_checks)
        bound = atom_target_bindings(bundle, manifest, item)
        for t in [*item.citation_target_ids, *item.candidate_target_ids]:
            if t not in bound:
                raise ValueError("atom target is not bound to this atom's own evidence")
        if item.kind == "quantity":
            span = spans.get(item.numeric_span_id)
            if (span is None or span.state == "identifier" or (span.field_path, span.start, span.end)
                    != (item.field_path, item.start, item.end)):
                raise ValueError("quantity atom does not match its numeric span")
            if item.numeric_span_id in quantity_spans:
                raise ValueError("number atomized twice")
            quantity_spans.add(item.numeric_span_id)
        else:
            if any(s.field_path == item.field_path and (s.start, s.end) == (item.start, item.end) for s in spans.values()):
                raise ValueError("a bare number is a quantity atom, not a fact")
            for other in facts.get(item.field_path, []):
                if item.start < other[1] and other[0] < item.end:
                    raise ValueError("overlapping fact atoms")
            facts.setdefault(item.field_path, []).append((item.start, item.end))
    asserted = {s.span_id for s in bundle.spans if s.field_path in scope and s.state != "identifier"}
    context = set(manifest.atomization.context_span_ids)
    if context & quantity_spans or context - asserted:
        raise ValueError("context spans must be asserted numbers that are not atoms")
    if manifest.atomization.coverage == "complete" and context:
        raise ValueError("complete coverage cannot set numbers aside as context")
    missing = asserted - quantity_spans - context
    if missing:
        raise ValueError(f"atomization omits {len(missing)} asserted number(s)")
    # Every bound check in scope is represented, so no control is orphaned.
    for f in bundle.form:
        if f.atom and f.atom.field_path in scope and f.atom.atom_id not in ids:
            raise ValueError("a fact check has no atom row")
        if f.numeric_span_id and f.numeric_span_id in asserted and f.numeric_span_id not in quantity_spans:
            raise ValueError("a quantity check has no atom row")
    fact_ids = {i.atom_id for i in manifest.items if i.kind == "fact"}
    if any(set(d.atom_ids) - fact_ids for d in manifest.citations):
        raise ValueError("declared atom citation is attributed to an unknown fact atom")
    return manifest


PREPARATION_PROVENANCE = ("Independently prepared for review and attributed to this exact occurrence; not "
                          "supplied by the answer, not a support judgment, and it never checks a row.")


def atom_view(bundle, manifest, render_manifests=()):
    """Browser rows: one atom per row with its evidence, calculation and targets."""
    targets = {t.target_id: t for m in render_manifests for t in m.targets}
    cited = {citation_target_id(bundle, c): c for c in bundle_citations(bundle)}
    cited.update({d.target_id: d.citation for d in manifest.citations})
    rendered = {m.source_id for m in render_manifests}
    rows = []
    for item in manifest.items:
        calc = _calculation_for(bundle, item.calculation_ref)
        bindings = atom_target_bindings(bundle, manifest, item)

        def describe(tid, binding=None):
            t = targets.get(tid)
            if t is None:
                c = cited.get(tid)
                frozen = {"source_id": c.source_id, "start_line": c.start_line, "end_line": c.end_line,
                          "excerpt": c.excerpt} if c else {}
                out = {"target_id": tid, "status": "frozen_text", **frozen, "rendered": False,
                       "reason": "No rendered source supplied for this citation; the frozen text line opens instead."}
            else:
                out = {"target_id": tid, "source_id": t.source_id, "status": t.status, "reason": t.reason,
                       "start_line": t.start_line, "end_line": t.end_line, "page": t.page,
                       "rendered": t.source_id in rendered}
            if binding:
                out["binding"] = binding
            return out

        def leaves_of(calculation, binding):
            out = []
            for leaf in calculation_citations(calculation) if calculation is not None else ():
                tid = citation_target_id(bundle, leaf)
                out.append({"source_id": leaf.source_id, "status": leaf.status, "reason": leaf.reason,
                            "start_line": leaf.start_line, "end_line": leaf.end_line,
                            "target": describe(tid, binding) if tid else None})
            return out

        preparations = []
        for k, ref in enumerate(item.prepared_refs):
            span, prepared = _prepared_for(bundle, ref)
            noun = "calculation" if prepared.calculation else "evidence"
            preparations.append({
                "ref": ref, "span_id": span.span_id, "origin": prepared.origin,
                "label": f"Independently prepared {noun}" + (f" {k + 1} of {len(item.prepared_refs)}"
                                                            if len(item.prepared_refs) > 1 else ""),
                "provenance": PREPARATION_PROVENANCE, "reason": prepared.reason,
                "targets": [describe(t, "prepared_evidence") for t in _located_ids(bundle, prepared.citations)],
                "calculation": prepared.calculation.model_dump(mode="json") if prepared.calculation else None,
                "calculation_leaves": leaves_of(prepared.calculation, "prepared_calculation_input")})
        rows.append({**item.model_dump(mode="json"), "prepared_refs": list(item.prepared_refs),
                     "targets": [describe(t, bindings.get(t)) for t in item.citation_target_ids],
                     "candidates": [describe(t, bindings.get(t)) for t in item.candidate_target_ids],
                     "calculation": calc.model_dump(mode="json") if calc else None,
                     "calculation_leaves": leaves_of(calc, "calculation_input"),
                     # Kept apart from the original candidate evidence above; never merged into it.
                     "preparations": preparations})
    return {"schema_version": manifest.schema_version, "provenance": manifest.provenance,
            "atomization": manifest.atomization.model_dump(mode="json"), "rows": rows,
            "verification_note": "Opening a source, matching a number or recomputing arithmetic never checks a row."}
