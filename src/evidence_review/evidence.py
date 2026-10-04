"""Deterministic inventory, passage navigation and bounded Decimal arithmetic."""

import ast
import re
from decimal import Decimal, InvalidOperation
from .contracts import NumericSpan

NUMBER = re.compile(
    r"(?<!\d)(?:[$£€]\s*)?[+−-]?\d+(?:[,.]\d+)*(?:\s*[–-]\s*\d+(?:[,.]\d+)*)?(?:\s*(?:%|bps\b|basis points\b|billion\b|million\b|thousand\b|[mbk]\b))?",
    re.I,
)


def numeric_spans(path, text):
    out = []
    for m in NUMBER.finditer(text):
        prefix = text[max(0, m.start() - 3) : m.start()]
        identifier = bool(prefix and prefix[-1].isalpha()) or bool(
            re.fullmatch(r"(?:19|20)\d{2}", m.group())
        )
        out.append(
            NumericSpan(
                span_id=f"{path}:{m.start()}:{m.end()}",
                field_path=path,
                start=m.start(),
                end=m.end(),
                text=m.group(),
                state="identifier" if identifier else "uncited",
            )
        )
    return out


def passage(source, start, end, context=2):
    lines = source.text.splitlines()
    if start < 1 or end < start or end > len(lines):
        raise ValueError("citation line bounds invalid")
    lo, hi = max(1, start - context), min(len(lines), end + context)
    headers = []
    if lines[start - 1].lstrip().startswith("|"):
        top = start
        while top > 1 and lines[top - 2].lstrip().startswith("|"):
            top -= 1
        headers = list(range(top, min(top + 2, start)))
    indices = sorted(set(headers + list(range(lo, hi + 1))))
    return {
        "text": "\n".join(lines[start - 1 : end]),
        "context": "\n".join(f"L{i}: {lines[i - 1]}" for i in indices),
        "full_text": source.text,
        "start_line": start,
        "end_line": end,
        "table_header_is_heuristic": bool(headers),
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
