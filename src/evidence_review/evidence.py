"""Deterministic inventory, passage navigation and bounded Decimal arithmetic."""

import ast
import decimal
import functools
import re
from decimal import Decimal, InvalidOperation
from .contracts import NumericSpan, canonical_json

# The evaluator's own arithmetic, applied through ``decimal.localcontext`` on every
# call whatever the caller's thread context says (precision, rounding, exponent
# limits, traps, exponent capitalisation). Its values equal CPython's default
# context, so every result produced before the semantics were pinned under default
# settings — sealed bundle recomputations and 0.6.0 worksheets — is reproduced
# digit for digit. Changing any field is a new semantics id, never an edit to v1.
ARITHMETIC_CONTEXT = decimal.Context(
    prec=28, rounding=decimal.ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0,
    flags=[], traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow])
# Recorded on every new worksheet computation. "finite" adds the check that is
# independent of any trap: a NaN, an infinity or a magnitude above Emax is a
# calculation error, never a result.
ARITHMETIC_SEMANTICS = ("decimal-v1:prec=28,rounding=ROUND_HALF_EVEN,Emin=-999999,Emax=999999,"
                        "capitals=1,clamp=0,traps=InvalidOperation+DivisionByZero+Overflow,finite")


def arithmetic():
    """Enter the evaluator's pinned context (a copy; the caller's is restored on exit)."""
    return decimal.localcontext(ARITHMETIC_CONTEXT)


def deterministic(fn):
    """Run ``fn`` under the pinned arithmetic context, never the ambient one."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with arithmetic():
            return fn(*args, **kwargs)
    return wrapper

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


@deterministic
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
    for span in bundle.spans:
        for operand in span.prepared_inputs:
            cites = ([operand.citation] if operand.citation else [])
            if operand.calculation:
                cites += list(calculation_citations(operand.calculation))
            for c in cites:
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


@deterministic
def evaluate(formula, values):
    """Bounded Decimal evaluation of +, -, *, / over named values; never ``eval``.

    Raises on unsupported syntax, unknown names and arithmetic faults."""
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

    return visit(tree)


SAFE_FORMULA_NODES = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.UnaryOp, ast.UAdd,
                      ast.USub, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div)


class UnsafeFormula(ValueError):
    """A formula outside the evaluator's grammar; refused, never stored or run."""


def formula_names(formula):
    """Names a formula reads, after a structural check against ``evaluate``'s grammar.

    Raises ``SyntaxError`` for an incomplete or malformed expression and
    ``UnsafeFormula`` for any construct (call, attribute, power, comparison,
    string, boolean, ...) the bounded evaluator does not accept."""
    if len(formula) > 1000:
        raise UnsafeFormula("formula too long")
    tree = ast.parse(formula, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise UnsafeFormula("formula too complex")
    for node in nodes:
        if not isinstance(node, SAFE_FORMULA_NODES) or (
            isinstance(node, ast.Constant) and type(node.value) not in (int, float)
        ):
            raise UnsafeFormula("unsupported formula syntax: " + type(node).__name__)
    return sorted({n.id for n in nodes if isinstance(n, ast.Name)})


@deterministic
def calculate(formula, values, reported, tolerance):
    try:
        result = evaluate(formula, values)
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


WORKSHEET_NOTE = ("Reviewer-authored arithmetic over reviewer-entered inputs. It does not "
                  "establish that the subject is supported, and it never changes a supplied formula.")


@deterministic
def _number(text):
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


@deterministic
def worksheet_computation(ws):
    """Derived outcome of one reviewer worksheet; inputs are never altered.

    ``invalid``: the inputs cannot be evaluated yet (no formula, malformed
    expression, undeclared name, non-numeric value). ``incomplete``: the formula
    reads an unavailable or blank input. ``calculation_error``: evaluation failed
    (division by zero is retained here, never coerced). ``computed``: a Decimal
    result, compared with the reported value only when one was entered. Every
    outcome names its ``arithmetic`` semantics (``ARITHMETIC_SEMANTICS``)."""
    base = {"evaluator": "evidence_review.evidence.evaluate", "arithmetic": ARITHMETIC_SEMANTICS, "result": None, "comparison": "not_compared",
            "discrepancy": None, "reason": "", "unavailable": [], "evidence_verified": False,
            "note": WORKSHEET_NOTE}
    formula = ws.formula.strip()
    if not formula:
        return {**base, "status": "invalid", "reason": "no formula entered"}
    try:
        names = formula_names(formula)
    except SyntaxError:
        return {**base, "status": "invalid", "reason": "formula syntax incomplete or invalid"}
    operands = {o.name: o for o in ws.operands}
    undeclared = [n for n in names if n not in operands]
    if undeclared:
        return {**base, "status": "invalid", "reason": "undeclared operand: " + ", ".join(undeclared)}
    unavailable = [n for n in names if operands[n].availability == "unavailable" or not operands[n].value.strip()]
    malformed = [n for n in names if n not in unavailable and _number(operands[n].value) is None]
    if malformed:
        return {**base, "status": "invalid", "reason": "operand value is not a decimal number: " + ", ".join(malformed)}
    if unavailable:
        return {**base, "status": "incomplete", "unavailable": unavailable,
                "reason": "unavailable input: " + ", ".join(unavailable)}
    try:
        result = evaluate(formula, {n: operands[n].value.strip() for n in names})
    except ArithmeticError as exc:
        # C decimal reports 0/0 as InvalidOperation carrying the DivisionUndefined signal.
        signals = exc.args[0] if exc.args and isinstance(exc.args[0], list) else []
        if isinstance(exc, ZeroDivisionError) or decimal.DivisionUndefined in signals:
            reason = "division by zero"
        elif isinstance(exc, decimal.Overflow) or decimal.Overflow in signals:
            reason = "result outside the decimal range"
        else:
            reason = "arithmetic error: " + type(exc).__name__
        return {**base, "status": "calculation_error", "reason": reason}
    # Independent of traps: no NaN, infinity or out-of-range magnitude is ever a result.
    if not result.is_finite():
        return {**base, "status": "calculation_error", "reason": "non-finite result"}
    if result and result.adjusted() > ARITHMETIC_CONTEXT.Emax:
        return {**base, "status": "calculation_error", "reason": "result outside the decimal range"}
    out = {**base, "status": "computed", "result": str(result)}
    reported, tolerance = _number(ws.reported_value), _number(ws.tolerance)
    if ws.reported_value.strip() == "":
        return {**out, "reason": "no reported value entered"}
    if reported is None or tolerance is None or tolerance < 0:
        return {**out, "reason": "reported value or tolerance is not a non-negative decimal"}
    compared = calculate(formula, {n: operands[n].value.strip() for n in names},
                         ws.reported_value.strip(), ws.tolerance.strip())
    return {**out, "comparison": compared["status"], "discrepancy": compared.get("discrepancy")}


def sealed_computation(worksheet, stored):
    """The stored computation of a sealed worksheet, proven by deterministic replay.

    Replays ``worksheet``'s inputs and returns ``stored`` unchanged (the exact
    object, so export bytes are the sealed bytes) only if the replay agrees with
    it byte for byte. A record written before semantics were recorded (0.6.0,
    no ``arithmetic`` key) is compared without that key. Any disagreement or an
    unknown semantics id raises naming the worksheet; nothing is recomputed into
    the record."""
    label = "worksheet " + worksheet.worksheet_id
    semantics = stored.get("arithmetic") if isinstance(stored, dict) else None
    replay = worksheet_computation(worksheet)
    if isinstance(stored, dict) and "arithmetic" not in stored:
        replay.pop("arithmetic")
    elif semantics != ARITHMETIC_SEMANTICS:
        raise ValueError(f"{label}: unknown arithmetic semantics {semantics!r} in sealed computation; refusing to replay it")
    if canonical_json(stored) != canonical_json(replay):
        raise ValueError(f"{label}: sealed computation does not match its deterministic replay; "
                         "refusing to export it rather than rewriting the sealed record")
    return stored


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
    prepared_operands = [o for n in asserted for o in n.prepared_inputs]
    prepared_cites = []
    for o in prepared_operands:
        if o.citation:
            prepared_cites.append(o.citation)
        if o.calculation:
            prepared_cites += list(calculation_citations(o.calculation))
    return {'prepared_inputs': len(prepared_operands), 'prepared_input_source_leaves': len(prepared_cites),
            'prepared_input_source_leaves_with_links': sum(exact(c) for c in prepared_cites),
            'asserted_spans': len(asserted), 'cited_spans': sum(bool(n.citations) for n in asserted),
            'direct_spans': direct, 'derived_spans': derived, 'cited_operands': operands,
            'cited_operands_with_links': linked, 'unresolved': unresolved,
            'evidence_verified': False}
