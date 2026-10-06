"""Deterministic inventory, passage navigation and bounded Decimal arithmetic."""

import ast
import re
from decimal import Decimal, InvalidOperation
from .contracts import NumericSpan

MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
# Calendar dates are one occurrence, never split into separate numbers.
DATE = (
    r"\d{4}-\d{2}-\d{2}"
    rf"|{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH},?\s+\d{{4}}"
)
VALUE = r"\d+(?:[,.]\d+)*"
SCALE = r"%|percent(?:age points?)?\b|per\s+cent\b|pp\b|bps\b|basis\s+points?\b|trillion\b|billion\b|million\b|thousand\b|tn\b|bn\b|mn\b|[mbkx]\b"
NUMBER = re.compile(
    rf"(?P<date>{DATE})"
    rf"|(?P<ratio>(?<![\d.]){VALUE}:\d+(?![\d:]))"
    rf"|(?P<amount>\((?:[$£€¥₹]\s*)?{VALUE}(?:\s*(?:{SCALE}))?\)"
    rf"|(?<!\d)(?:[$£€¥₹]\s*)?[+−-]?(?:[$£€¥₹]\s*)?{VALUE}"
    rf"(?:\s*[–-]\s*(?:[$£€¥₹]\s*)?{VALUE})?(?:\s*(?:{SCALE}))?)",
    re.I,
)
# Words that make the following numeral a reference, not a quantity.
REFERENCE_WORDS = re.compile(
    r"(?:item|note|section|page|exhibit|part|rule|form|appendix|footnote|figure)\s*$",
    re.I,
)


def _identifier(path, text, m):
    if m["date"]:
        return True, "calendar date"
    if path.endswith((".cutoff", ".period")):
        return True, "date or period field"
    value = m.group()
    before, after = text[: m.start()], text[m.end() : m.end() + 2]
    if value[0].isdigit() and before[-1:].isalpha():
        return True, "attached to a label"
    if value[0].isdigit() and re.match(r"-[A-Za-z]", after):
        return True, "form or document designation"
    if REFERENCE_WORDS.search(before[-12:]):
        return True, "document reference"
    if re.fullmatch(r"0\d+", value):
        return True, "zero-padded code"
    # Magnitude alone cannot distinguish years from economic counts.
    return False, ""


def numeric_spans(path, text):
    out = []
    for m in NUMBER.finditer(text):
        identifier, reason = _identifier(path, text, m)
        out.append(
            NumericSpan(
                span_id=f"{path}:{m.start()}:{m.end()}",
                field_path=path,
                start=m.start(),
                end=m.end(),
                text=m.group(),
                state="identifier" if identifier else "uncited",
                reason=reason,
            )
        )
    return out


NOTE_LINE = re.compile(
    r"^\s*(?:\*|†|‡|§|¹|²|³|\(\w{1,3}\)|\[\w{1,3}\]|\d{1,2}[.)]\s|(?:foot)?notes?\b|source\b|n/?m\b)",
    re.I,
)


def _table(lines, i):
    return lines[i - 1].lstrip().startswith("|")


def passage(source, start, end, context=2, paragraph=8):
    """Cited lines plus the paragraph, table header and table notes around them.

    Roles are deterministic layout hints; header and note discovery is
    heuristic, so the full source text is always returned as well.
    """
    lines = source.text.splitlines()
    if start < 1 or end < start or end > len(lines):
        raise ValueError("citation line bounds invalid")
    lo, hi = max(1, start - context), min(len(lines), end + context)
    # Extend to the enclosing paragraph (blank-line bounded), within limits.
    while lo > 1 and lines[lo - 2].strip() and start - lo < paragraph:
        lo -= 1
    while hi < len(lines) and lines[hi].strip() and hi - end < paragraph:
        hi += 1
    roles = {}
    if _table(lines, start):
        top = start
        while top > 1 and _table(lines, top - 1):
            top -= 1
        for i in range(top, min(top + 2, start)):
            roles[i] = "table_header"
        bottom = end
        while bottom < len(lines) and _table(lines, bottom + 1):
            bottom += 1
        i = bottom + 1
        while i <= len(lines) and i - bottom <= paragraph:
            if NOTE_LINE.match(lines[i - 1]):
                roles[i] = "note"
            elif lines[i - 1].strip():
                break
            i += 1
    for i in range(lo, hi + 1):
        roles.setdefault(
            i,
            "cited"
            if start <= i <= end
            else ("note" if NOTE_LINE.match(lines[i - 1]) else "context"),
        )
    indices = sorted(roles)
    return {
        "text": "\n".join(lines[start - 1 : end]),
        "context": "\n".join(f"L{i}: {lines[i - 1]}" for i in indices),
        "lines": [{"line": i, "text": lines[i - 1], "role": roles[i]} for i in indices],
        "full_text": source.text,
        "start_line": start,
        "end_line": end,
        "table_header_is_heuristic": "table_header" in roles.values(),
    }


def validate_citation(citation, sources):
    if citation.status != "located":
        if not citation.reason:
            raise ValueError("unresolved citation requires reason")
        return
    if citation.source_id not in sources:
        raise ValueError("unknown located source")
    if citation.start_line is None or citation.end_line is None:
        raise ValueError("located citation needs lines")
    text = passage(sources[citation.source_id], citation.start_line, citation.end_line)[
        "text"
    ]
    if not citation.excerpt or " ".join(citation.excerpt.split()) not in " ".join(
        text.split()
    ):
        raise ValueError("located excerpt mismatch")


def operand_evidence(operands, unit, conversions):
    """Report what evidence exists for each input; never certify support."""
    missing = []
    for o in operands:
        if o.kind == "constant":
            continue
        if o.kind == "derived":
            r = o.calculation.recomputation
            try:
                agrees = Decimal(o.value) == Decimal(o.calculation.result)
            except InvalidOperation:
                agrees = False
            if r["status"] != "match" or not agrees:
                missing.append(o.name)
        elif o.citation is None or o.citation.status != "located":
            missing.append(o.name)
    units = sorted({o.unit for o in operands if o.unit and o.kind != "constant"})
    warnings = []
    if not conversions and len(units) > 1:
        warnings.append("operands use different units without a declared conversion")
    if any(not o.unit for o in operands if o.kind != "constant"):
        warnings.append("operand unit unavailable")
    if any(not o.period for o in operands if o.kind != "constant"):
        warnings.append("operand period unavailable")
    return {
        "status": "operand_evidence_missing" if missing else "all_operands_cited",
        "missing": missing,
        "warnings": warnings,
        "note": "Arithmetic and located inputs do not establish that the claim is supported.",
    }


def calculation_citations(calc):
    for operand in calc.operands:
        if operand.citation:
            yield operand.citation
        if operand.calculation:
            yield from calculation_citations(operand.calculation)


def evidence_views(bundle):
    """Context for every located citation, keyed source:start:end, for display."""
    sources = {s.source_id: s for s in bundle.sources}
    views = {}
    prepared = [p for n in bundle.spans for p in n.prepared_evidence]
    for item in [*bundle.claims, *bundle.references, *bundle.spans, *prepared]:
        cites = list(item.citations)
        if item.calculation:
            cites += list(calculation_citations(item.calculation))
        for c in cites:
            if c.status == "located":
                key = f"{c.source_id}:{c.start_line}:{c.end_line}"
                view = passage(sources[c.source_id], c.start_line, c.end_line)
                view.pop("full_text")
                from .table_context import table_context
                view["table"] = table_context(sources[c.source_id], c.start_line, c.end_line)
                views[key] = view
    return views


def inventory(bundle):
    """Deterministic evidence counts; zero means not observed, not verified."""
    spans = {}
    bound = 0
    for n in bundle.spans:
        spans[n.state] = spans.get(n.state, 0) + 1
        bound += bool(n.citations or n.calculation)
    unresolved_claims = []
    inaccessible = 0
    failed = []
    for item in [*bundle.claims, *bundle.references]:
        ident = getattr(item, "claim_id", None) or item.reference_id
        cites = list(item.citations)
        if item.calculation:
            cites += list(calculation_citations(item.calculation))
            r = item.calculation.recomputation
            if r.get("status") != "match":
                failed.append(ident)
            if r.get("evidence", {}).get("status") != "all_operands_cited":
                unresolved_claims.append(ident)
        bad = [c for c in cites if c.status != "located"]
        inaccessible += len(bad)
        if (bad or not cites) and ident not in unresolved_claims:
            unresolved_claims.append(ident)
    return {
        "spans_by_state": dict(sorted(spans.items())),
        "span_total": len(bundle.spans),
        "spans_with_own_evidence": bound,
        "claims": len(bundle.claims),
        "references": len(bundle.references),
        "unresolved_subjects": sorted(unresolved_claims),
        "unresolved_citations": inaccessible,
        "calculations_not_matching": sorted(failed),
        "span_calculations_not_matching": sorted(
            n.span_id
            for n in bundle.spans
            if n.calculation and n.calculation.recomputation.get("status") != "match"
        ),
    }


def calculate(formula, values, reported, tolerance):
    try:
        if len(formula) > 1000:
            raise ValueError("formula too long")
        tree = ast.parse(formula, mode="eval")
        if len(list(ast.walk(tree))) > 100:
            raise ValueError("formula too complex")

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return Decimal(ast.get_source_segment(formula, node))
            if isinstance(node, ast.Name):
                return Decimal(values[node.id])
            if isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.UAdd, ast.USub)
            ):
                value = visit(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
            ):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add):
                    return a + b
                if isinstance(node.op, ast.Sub):
                    return a - b
                if isinstance(node.op, ast.Mult):
                    return a * b
                return a / b
            raise ValueError("unsupported formula syntax")

        result = visit(tree)
        delta = result - Decimal(reported)
        tol = Decimal(tolerance)
        if (
            not result.is_finite()
            or not delta.is_finite()
            or not tol.is_finite()
            or tol < 0
        ):
            raise ValueError("non-finite arithmetic or invalid tolerance")
        return {
            "status": "match" if abs(delta) <= tol else "mismatch",
            "result": str(result),
            "discrepancy": str(delta),
            "evidence_verified": False,
        }
    except (ValueError, KeyError, SyntaxError, ArithmeticError) as exc:
        return {
            "status": "unresolved",
            "result": None,
            "reason": str(exc),
            "evidence_verified": False,
        }


def navigation_coverage(bundle, links):
    """Exposure destinations on this exact served bundle, never support verdicts."""
    def exact(c):
        return c.status == 'located' and bool(links.get(c.source_id, {}).get('line_url'))

    asserted = [n for n in bundle.spans if n.state != 'identifier']
    direct, derived, operands, linked, unresolved = 0, 0, 0, 0, []
    for n in asserted:
        if n.calculation:
            derived += 1
            for c in calculation_citations(n.calculation):
                operands += 1
                linked += exact(c)
                if not exact(c):
                    unresolved.append({'span_id': n.span_id, 'reason': c.reason or 'No exact operand source link'})
            missing = n.calculation.recomputation['evidence']['missing']
            if missing:
                unresolved.append({'span_id': n.span_id, 'reason': 'No source located: ' + ', '.join(missing)})
        elif n.state == 'cited' and len(n.citations) == 1 and exact(n.citations[0]):
            direct += 1
        else:
            unresolved.append({'span_id': n.span_id, 'reason': n.reason or (
                'Multiple possible sources—no exact match established' if n.state == 'ambiguous'
                else 'Choose among cited sources' if len(n.citations) > 1
                else 'No exact source link' if n.citations else 'No source located')})
    return {'asserted_spans': len(asserted), 'cited_spans': sum(bool(n.citations) for n in asserted),
            'direct_spans': direct, 'derived_spans': derived, 'cited_operands': operands,
            'cited_operands_with_links': linked, 'unresolved': unresolved,
            'evidence_verified': False}
