"""Conservative Markdown table context with original line provenance.

Only a single unique header, separator and rectangular, uniquely labelled rows
are parsed. Anything requiring column or merged-cell inference stays raw.
"""
import re
from .evidence import NOTE_LINE


def _cells(text):
    # Code spans and HTML tables have different pipe semantics; never guess.
    if '`' in text or '<' in text or '>' in text:
        return None
    text = text.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|') and not text.endswith(r'\|'):
        text = text[:-1]
    return [cell.strip().replace(r'\|', '|') for cell in re.split(r'(?<!\\)\|', text)]


def table_context(source, start_line, end_line):
    lines = source.text.splitlines()
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError('citation line bounds invalid')
    top, bottom = start_line - 1, end_line - 1
    looks_table = lines[top].lstrip().startswith('|')
    if looks_table:
        while top > 0 and lines[top - 1].lstrip().startswith('|'):
            top -= 1
        while bottom + 1 < len(lines) and lines[bottom + 1].lstrip().startswith('|'):
            bottom += 1
    lo, hi = max(0, top - 2), min(len(lines), bottom + 3)
    context = [{'line': i + 1, 'text': lines[i]} for i in range(lo, top) if lines[i].strip()]
    notes = []
    i = bottom + 1
    while i < len(lines) and i <= bottom + 8:
        if NOTE_LINE.match(lines[i]):
            notes.append({'line': i + 1, 'text': lines[i]})
            hi = max(hi, i + 1)
        elif lines[i].strip():
            break
        i += 1
    result = {'status': 'unparsed', 'reason': 'not a table',
              'lines': [{'line': i + 1, 'text': lines[i]} for i in range(lo, hi)],
              'headers': [], 'rows': [], 'context': context, 'notes': notes}
    if not looks_table:
        return result
    grid = [_cells(lines[i]) for i in range(top, bottom + 1)]
    result['reason'] = 'Unsupported or ambiguous table structure; no column identity established'
    if len(grid) < 3 or any(row is None for row in grid):
        return result
    header, separator, *rows = grid
    if (not all(header) or len(set(header)) != len(header)
            or len(separator) != len(header)
            or not all(re.fullmatch(r':?-{3,}:?', cell) for cell in separator)
            or any(len(row) != len(header) for row in rows)
            or any(not row[0] or not any(row[1:]) for row in rows)
            or len({row[0] for row in rows}) != len(rows)
            or any(any(re.fullmatch(r':?-{3,}:?', cell) for cell in row) for row in rows)):
        return result
    result.update(status='parsed', reason='', headers=header,
                  rows=[{'line': top + j + 3, 'cells': row,
                         'cited': start_line <= top + j + 3 <= end_line}
                        for j, row in enumerate(rows)])
    return result
