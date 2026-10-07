"""Offline, bounded rendering of frozen sources into a sanitized derivative with exact targets.

The derivative is a JSON node tree with a closed tag vocabulary. The browser
builds it with ``createElement``/``textContent``, so nothing from a source can
execute, fetch a resource or carry an attribute outside the allowlist. Original
bytes are never modified; every transform is listed in the manifest.

Mapping is deterministic: a citation (frozen source lines plus excerpt) becomes
an ``exact`` target only when one rendered node set is proven to hold it.
Duplicates stay ``ambiguous`` with every candidate, a scanned page without a
text layer is ``page_only`` at best, and anything unproven is ``unavailable``.
Nothing here decides whether a source supports a statement.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .atomic_evidence import (
    RENDER_MANIFEST_SCHEMA,
    RenderedSourceTarget,
    SourceRenderManifest,
    bundle_citations,
    target_identity,
    validate_render_manifest,
)
from .contracts import digest
from .table_context import _cells

RENDERER = {"name": "evidence-review/source-rendering", "version": "1"}
DERIVATIVE_SCHEMA = "rendered-source/v1"
# Closed vocabulary the browser knows how to build; anything else is unwrapped or dropped.
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "caption", "figcaption", "dt", "dd",
              "pre", "blockquote"}
CONTAINER_TAGS = {"section", "article", "div", "ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr",
                  "figure", "main", "header", "footer", "aside", "nav"}
INLINE_TAGS = {"span", "em", "strong", "b", "i", "u", "sup", "sub", "code", "small", "abbr", "mark", "s", "q",
               "cite", "time", "a"}
VOID_TAGS = {"br", "hr"}
ALLOWED_TAGS = BLOCK_TAGS | CONTAINER_TAGS | INLINE_TAGS | VOID_TAGS | {"page", "glyphs", "line", "notice"}
# Elements whose whole subtree is removed: executable, remote-loading or interactive.
DROPPED = {"script", "style", "noscript", "template", "iframe", "frame", "frameset", "object", "embed", "applet",
           "svg", "math", "canvas", "video", "audio", "source", "track", "picture", "link", "meta", "base",
           "head", "title", "input", "select", "textarea", "button", "option", "datalist", "output", "dialog",
           "portal", "img", "image", "map", "area"}
SAFE_ATTRS = {"colspan": r"^[1-9][0-9]?$", "rowspan": r"^[1-9][0-9]?$", "scope": r"^(row|col|rowgroup|colgroup)$"}


class RenderLimitExceeded(ValueError):
    """Input exceeded a declared bound; rendering stops instead of hanging or exhausting memory."""


@dataclass(frozen=True)
class RenderLimits:
    max_bytes: int = 5_000_000
    max_pages: int = 200
    max_nodes: int = 50_000
    # Parser work units (tokens/objects); bounds CPU deterministically, unlike a wall clock.
    max_work: int = 2_000_000


@dataclass(frozen=True)
class OriginalAsset:
    """Caller-supplied eligible local bytes of a source's original; never a URL to fetch."""
    data: bytes
    media_type: str
    declared_sha256: str | None = None
    # True when the bundle's frozen text was produced from these bytes by the caller's normalizer.
    frozen_derived: bool = True
    # Optional frozen line -> PDF page map (e.g. from the caller's OCR), used only for page-only targets.
    page_map: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedSourceAsset:
    manifest: SourceRenderManifest
    derivative: dict

    def verify(self):
        """Refuse a derivative whose bytes no longer match the manifest it was prepared with."""
        if digest(self.derivative) != self.manifest.derivative_sha256:
            raise ValueError("rendered derivative changed after preparation")
        return self


class _Budget:
    def __init__(self, limits):
        self.limits, self.work, self.nodes = limits, 0, 0

    def step(self, n=1):
        self.work += n
        if self.work > self.limits.max_work:
            raise RenderLimitExceeded("source too complex to render within the declared work bound")

    def node(self):
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise RenderLimitExceeded("rendered source exceeds the declared node bound")
        return f"n{self.nodes}"


def _norm(text):
    return " ".join(text.split())


def _text_of(node):
    if "text" in node:
        return node["text"]
    return " ".join(_text_of(c) for c in node.get("children", []))


# ---------------------------------------------------------------- Markdown / text


def _md_line_tree(text, budget, transforms):
    """Line-faithful rendering: every frozen line maps to exactly one node."""
    lines = text.splitlines()
    nodes, line_nodes, cells_by_line = [], {}, {}
    i = 0

    def leaf(tag, line_no, content, **extra):
        budget.step()
        nid = budget.node()
        line_nodes[line_no] = nid
        return {"id": nid, "tag": tag, "line": line_no, "text": content, **extra}

    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", raw)
        if heading:
            nodes.append(leaf(f"h{len(heading.group(1))}", i + 1, heading.group(2).strip()))
            i += 1
            continue
        if raw.lstrip().startswith("|"):
            top = i
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                i += 1
            grid = [_cells(lines[j]) for j in range(top, i)]
            proven = (len(grid) >= 2 and all(g is not None for g in grid)
                      and all(re.fullmatch(r":?-{3,}:?", c) for c in grid[1])
                      and len({len(g) for g in grid}) == 1 and all(grid[0]))
            if not proven:
                transforms.append(f"lines {top + 1}-{i}: table structure not proven; shown as numbered text")
                block = {"id": budget.node(), "tag": "pre", "children": [
                    leaf("line", j + 1, lines[j]) for j in range(top, i)]}
                nodes.append(block)
                continue
            transforms.append(f"lines {top + 1}-{i}: Markdown pipe table rendered as a table")
            head = {"id": budget.node(), "tag": "tr", "line": top + 1, "children": []}
            line_nodes[top + 1] = head["id"]
            for c in grid[0]:
                budget.step()
                head["children"].append({"id": budget.node(), "tag": "th", "text": c, "attrs": {"scope": "col"}})
            body = []
            for j in range(top + 2, i):
                row = {"id": budget.node(), "tag": "tr", "line": j + 1, "children": []}
                line_nodes[j + 1] = row["id"]
                cells_by_line[j + 1] = []
                for k, c in enumerate(grid[j - top]):
                    budget.step()
                    cell = {"id": budget.node(), "tag": "th" if k == 0 else "td", "text": c}
                    if k == 0:
                        cell["attrs"] = {"scope": "row"}
                    row["children"].append(cell)
                    cells_by_line[j + 1].append(cell)
                body.append(row)
            # The separator line has no visible node; it maps to the header row.
            line_nodes[top + 2] = head["id"]
            nodes.append({"id": budget.node(), "tag": "table", "children": [
                {"id": budget.node(), "tag": "thead", "children": [head]},
                {"id": budget.node(), "tag": "tbody", "children": body}]})
            continue
        item = re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.*)$", raw)
        if item:
            ordered = bool(re.match(r"^\s*\d", raw))
            items = []
            while i < len(lines) and re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", lines[i]) \
                    and bool(re.match(r"^\s*\d", lines[i])) == ordered:
                items.append(leaf("li", i + 1, re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.*)$", lines[i]).group(1)))
                i += 1
            nodes.append({"id": budget.node(), "tag": "ol" if ordered else "ul", "children": items})
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("|", "#")) \
                and not re.match(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", lines[i]):
            para.append(leaf("line", i + 1, lines[i]))
            i += 1
        nodes.append({"id": budget.node(), "tag": "p", "children": para})
    transforms.append("inline Markdown markup shown literally")
    return nodes, line_nodes, cells_by_line


def _line_targets(source, citations, line_nodes, cells_by_line, raw_sha):
    lines = source.text.splitlines()
    out = []
    for c in citations:
        excerpt = _norm(c.excerpt)
        base = dict(target_id=target_identity(source.source_id, source.sha256, c.start_line, c.end_line, c.excerpt),
                    source_id=source.source_id, frozen_sha256=source.sha256, raw_sha256=raw_sha,
                    start_line=c.start_line, end_line=c.end_line, excerpt=c.excerpt)
        if c.end_line > len(lines) or any(n not in line_nodes for n in range(c.start_line, c.end_line + 1)):
            out.append(RenderedSourceTarget(**base, status="unavailable", reason="Cited lines have no rendered node"))
            continue
        nodes = list(dict.fromkeys(line_nodes[n] for n in range(c.start_line, c.end_line + 1)))
        reason = ""
        if c.start_line == c.end_line and c.start_line in cells_by_line:
            hits = [cell["id"] for cell in cells_by_line[c.start_line] if excerpt and excerpt in _norm(cell["text"])]
            if len(hits) == 1:
                nodes = hits
            elif len(hits) > 1:
                reason = f"Excerpt occurs in {len(hits)} cells of the cited row; the whole row is highlighted"
        out.append(RenderedSourceTarget(**base, dom_targets=nodes, status="exact", reason=reason))
    return out


# ---------------------------------------------------------------- HTML


class _Sanitizer(HTMLParser):
    def __init__(self, budget):
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.root = {"id": "root", "tag": "div", "children": []}
        self.stack = [self.root]
        self.drop_depth = 0
        self.removed = {}

    def _removed(self, what):
        self.removed[what] = self.removed.get(what, 0) + 1

    def handle_starttag(self, tag, attrs):
        self.budget.step()
        if self.drop_depth:
            if tag not in VOID_TAGS and tag not in ("img", "input", "meta", "link", "source", "track", "area", "base"):
                self.drop_depth += 1
            return
        if tag in DROPPED:
            self._removed(f"removed <{tag}>")
            if tag == "img":
                self.stack[-1]["children"].append({"id": self.budget.node(), "tag": "notice",
                                                   "text": "[image omitted from offline rendering]"})
            elif tag not in ("input", "meta", "link", "source", "track", "area", "base"):
                self.drop_depth = 1
            return
        for name, value in attrs:
            if name.startswith("on"):
                self._removed("removed event-handler attribute")
            elif name in ("href", "src", "srcset", "style", "action", "formaction", "background", "poster"):
                self._removed(f"removed {name} attribute")
        if tag not in ALLOWED_TAGS or tag in ("page", "glyphs", "line", "notice"):
            self._removed(f"unwrapped <{tag}>")
            return
        node = {"id": self.budget.node(), "tag": "span" if tag == "a" else tag, "children": []}
        if tag == "a":
            self._removed("made link inert")
        safe = {n: v for n, v in attrs if n in SAFE_ATTRS and v is not None and re.match(SAFE_ATTRS[n], v)}
        if safe:
            node["attrs"] = safe
        self.stack[-1]["children"].append(node)
        if tag in VOID_TAGS:
            return
        self.stack.append(node)

    def handle_endtag(self, tag):
        self.budget.step()
        if self.drop_depth:
            self.drop_depth -= 1
            return
        tag = "span" if tag == "a" else tag
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth]["tag"] == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        self.budget.step()
        if self.drop_depth or not data.strip():
            return
        self.stack[-1]["children"].append({"id": self.budget.node(), "tag": "span", "text": data})

    def handle_comment(self, data):
        self._removed("removed comment")


def _html_tree(data, budget, transforms):
    text = data.decode("utf-8", errors="replace")
    parser = _Sanitizer(budget)
    parser.feed(text)
    parser.close()
    for what, n in sorted(parser.removed.items()):
        transforms.append(f"{what} ({n})")
    return parser.root["children"]


def _walk(nodes, parent=None):
    for n in nodes:
        yield n, parent
        yield from _walk(n.get("children", []), n)


def _html_targets(source, citations, nodes, raw_sha):
    lines = source.text.splitlines()
    leaves, rows = [], []
    parents = {}
    for node, parent in _walk(nodes):
        parents[node["id"]] = parent
        if node["tag"] in BLOCK_TAGS:
            leaves.append(node)
        if node["tag"] == "tr":
            rows.append(node)
    out = []
    for c in citations:
        excerpt = _norm(c.excerpt)
        base = dict(target_id=target_identity(source.source_id, source.sha256, c.start_line, c.end_line, c.excerpt),
                    source_id=source.source_id, frozen_sha256=source.sha256, raw_sha256=raw_sha,
                    start_line=c.start_line, end_line=c.end_line, excerpt=c.excerpt)
        hits = [n for n in leaves if excerpt and excerpt in _norm(_text_of(n))]
        # Innermost only: a caption containing a cell's text is not a second occurrence.
        hits = [n for n in hits if not any(o is not n and any(d is o for d, _ in _walk(n.get("children", [])))
                                           for o in hits)]
        cited = "\n".join(lines[c.start_line - 1:c.end_line]) if c.end_line <= len(lines) else ""
        if len(hits) > 1 and cited.lstrip().startswith("|"):
            want = [_norm(x) for x in (_cells(cited) or [])]
            matching = [r for r in rows if [_norm(_text_of(ch)) for ch in r.get("children", [])] == want]
            if len(matching) == 1:
                hits = [h for h in hits if parents.get(h["id"]) is matching[0]]
        elif len(hits) > 1 and _norm(cited):
            whole = [h for h in hits if _norm(cited).strip("|# ").strip() in _norm(_text_of(h))]
            if len(whole) == 1:
                hits = whole
        if len(hits) == 1:
            out.append(RenderedSourceTarget(**base, dom_targets=[hits[0]["id"]], status="exact"))
        elif hits:
            out.append(RenderedSourceTarget(**base, status="ambiguous", candidates=[[h["id"]] for h in hits],
                                            reason=f"Excerpt occurs in {len(hits)} places in the original; none chosen"))
        else:
            out.append(RenderedSourceTarget(**base, status="unavailable",
                                            reason="Excerpt not found in the sanitized original"))
    return out


# ---------------------------------------------------------------- PDF (text layer only)


_OBJ = re.compile(rb"(\d+)\s+0\s+obj(.*?)endobj", re.S)
_TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z'\"*]+")


def _pdf_string(tok):
    if tok.startswith(b"<"):
        return bytes.fromhex(re.sub(rb"\s", b"", tok[1:-1]).decode()).decode("latin-1")
    body, out, i = tok[1:-1], bytearray(), 0
    esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
    while i < len(body):
        ch = body[i:i + 1]
        if ch == b"\\" and i + 1 < len(body):
            nxt = body[i + 1:i + 2]
            if nxt in esc:
                out += esc[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", body[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(), 8) & 0xFF)
                i += 1 + len(octal.group())
                continue
            i += 1
            continue
        out += ch
        i += 1
    return out.decode("latin-1")


def _pdf_objects(data, budget):
    objs = {}
    for m in _OBJ.finditer(data):
        budget.step()
        body = m.group(2)
        stream = None
        s = re.search(rb"stream\r?\n", body)
        if s:
            end = body.rfind(b"endstream")
            stream = body[s.end():end].rstrip(b"\r\n")
            head = body[:s.start()]
            if b"/FlateDecode" in head:
                d = zlib.decompressobj()
                stream = d.decompress(stream, budget.limits.max_bytes)
                if d.unconsumed_tail:
                    raise RenderLimitExceeded("PDF stream expands beyond the declared byte bound")
            elif re.search(rb"/Filter", head):
                stream = None  # Images and other encodings are not decoded.
            body = head
        objs[int(m.group(1))] = (body, stream)
    return objs


def _refs(text):
    return [int(x) for x in re.findall(rb"(\d+)\s+0\s+R", text)]


def _pdf_pages(objs, budget):
    root = next((b for b, _ in objs.values() if re.search(rb"/Type\s*/Pages\b", b) and b"/Parent" not in b), None)
    if root is None:
        raise ValueError("PDF page tree not found")
    pages, seen = [], set()

    def visit(body):
        kids = re.search(rb"/Kids\s*\[(.*?)\]", body, re.S)
        for ref in _refs(kids.group(1)) if kids else []:
            budget.step()
            if ref in seen or ref not in objs:
                continue
            seen.add(ref)
            kid = objs[ref][0]
            if re.search(rb"/Type\s*/Pages\b", kid):
                visit(kid)
            elif re.search(rb"/Type\s*/Page\b", kid):
                pages.append(ref)
                if len(pages) > budget.limits.max_pages:
                    raise RenderLimitExceeded("PDF exceeds the declared page bound")
    visit(root)
    return pages


def _font_widths(objs, page_body):
    fonts = {}
    res = re.search(rb"/Font\s*<<(.*?)>>", page_body, re.S)
    if not res:
        ref = re.search(rb"/Resources\s+(\d+)\s+0\s+R", page_body)
        res = re.search(rb"/Font\s*<<(.*?)>>", objs.get(int(ref.group(1)), (b"",))[0], re.S) if ref else None
    for name, ref in re.findall(rb"/([A-Za-z0-9_.+-]+)\s+(\d+)\s+0\s+R", res.group(1) if res else b""):
        body = objs.get(int(ref), (b"",))[0]
        first = re.search(rb"/FirstChar\s+(\d+)", body)
        widths = re.search(rb"/Widths\s*\[(.*?)\]", body, re.S)
        identity = b"Identity" in body or b"/Type0" in body
        fonts[name.decode()] = {
            "first": int(first.group(1)) if first else None,
            "widths": [float(w) for w in widths.group(1).split()] if widths else None,
            "unsupported": identity,
        }
    return fonts


def _pdf_runs(stream, fonts, budget):
    """Text runs with origin and (when widths are declared) a proven width."""
    runs, stack = [], []
    tm = [1, 0, 0, 1, 0, 0]
    lm = list(tm)
    font, size, leading = None, 0.0, 0.0
    open_list = False
    for raw in _TOKEN.findall(stream):
        budget.step()
        tok = raw
        if tok in (b"BT",):
            tm, lm = [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0]
            continue
        if tok in (b"Tf", b"Td", b"TD", b"Tm", b"TL", b"T*", b"Tj", b"TJ", b"'", b'"'):
            args, stack, open_list = stack, [], False
            if tok == b"Tf" and len(args) >= 2:
                font, size = args[0][1:].decode(errors="replace"), float(args[1])
            elif tok in (b"Td", b"TD") and len(args) >= 2:
                lm = [lm[0], lm[1], lm[2], lm[3], lm[4] + float(args[0]), lm[5] + float(args[1])]
                tm = list(lm)
                if tok == b"TD":
                    leading = -float(args[1])
            elif tok == b"Tm" and len(args) >= 6:
                lm = [float(a) for a in args[:6]]
                tm = list(lm)
            elif tok == b"TL" and args:
                leading = float(args[0])
            elif tok in (b"T*", b"'", b'"'):
                lm = [lm[0], lm[1], lm[2], lm[3], lm[4], lm[5] - leading]
                tm = list(lm)
            if tok in (b"Tj", b"'", b'"', b"TJ"):
                pool = args if tok != b"TJ" else next((a for a in reversed(args) if isinstance(a, list)), [])
                parts = [a for a in pool if isinstance(a, bytes) and a[:1] in (b"(", b"<")]
                text = "".join(_pdf_string(p) for p in parts)
                info = fonts.get(font or "", {})
                if info.get("unsupported"):
                    runs.append({"unsupported": True})
                    continue
                width = None
                if info.get("widths") is not None and info.get("first") is not None:
                    try:
                        width = sum(info["widths"][ord(ch) - info["first"]] for ch in text) / 1000 * size
                    except IndexError:
                        width = None
                x, y = tm[4], tm[5]
                runs.append({"text": text, "x": x, "y": y, "size": size, "width": width})
                if width is not None:
                    tm = [tm[0], tm[1], tm[2], tm[3], tm[4] + width, tm[5]]
            continue
        if tok == b"[":
            stack.append([])
            open_list = True
            continue
        if tok == b"]":
            open_list = False  # The list stays on the stack as one operand.
            continue
        if open_list and stack and isinstance(stack[-1], list):
            stack[-1].append(tok)
        elif re.fullmatch(rb"[-+]?\d*\.?\d+", tok) or tok[:1] in (b"(", b"<", b"/"):
            stack.append(tok)
        else:
            stack = []
    return runs


def _pdf_tree(data, budget, transforms):
    objs = _pdf_objects(data, budget)
    pages = []
    for number, ref in enumerate(_pdf_pages(objs, budget), start=1):
        body = objs[ref][0]
        box = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", body)
        width, height = (float(box.group(3)) - float(box.group(1)), float(box.group(4)) - float(box.group(2))) \
            if box else (612.0, 792.0)
        contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+0\s+R)", body)
        stream = b"\n".join(objs[r][1] or b"" for r in _refs(contents.group(1))) if contents else b""
        runs = _pdf_runs(stream, _font_widths(objs, body), budget)
        page = {"id": f"p{number}", "tag": "page", "page": number, "width": width, "height": height,
                "children": []}
        if any(r.get("unsupported") for r in runs):
            page["text_layer"] = "unsupported_encoding"
        for r in runs:
            if r.get("unsupported") or not r["text"].strip():
                continue
            node = {"id": budget.node(), "tag": "glyphs", "text": r["text"], "x": round(r["x"], 2),
                    "y": round(height - r["y"] - r["size"], 2), "size": round(r["size"], 2)}
            if r["width"] is not None:
                node["bbox"] = [round(r["x"], 2), round(r["y"], 2), round(r["x"] + r["width"], 2),
                                round(r["y"] + r["size"], 2)]
            page["children"].append(node)
        page.setdefault("text_layer", "present" if page["children"] else "absent")
        if page["text_layer"] != "present":
            page["children"].append({"id": budget.node(), "tag": "notice",
                                     "text": f"Page {number}: no extractable text layer (scanned image or unsupported "
                                             "font encoding); exact highlight unavailable."})
        pages.append(page)
    transforms.append("PDF text layer positioned at original coordinates; images, vector graphics and embedded fonts "
                      "are not rendered")
    return pages


def _pdf_targets(source, citations, pages, raw_sha, page_map):
    out = []
    lines = source.text.splitlines()
    runs = [(p, g) for p in pages for g in p["children"] if g["tag"] == "glyphs"]
    joined, spans, pos = [], [], 0
    for p, g in runs:
        t = _norm(g["text"])
        spans.append((pos, pos + len(t), p, g))
        joined.append(t)
        pos += len(t) + 1
    text = " ".join(joined)
    for c in citations:
        excerpt = _norm(c.excerpt)
        base = dict(target_id=target_identity(source.source_id, source.sha256, c.start_line, c.end_line, c.excerpt),
                    source_id=source.source_id, frozen_sha256=source.sha256, raw_sha256=raw_sha,
                    start_line=c.start_line, end_line=c.end_line, excerpt=c.excerpt)
        starts = [m.start() for m in re.finditer(re.escape(excerpt), text)] if excerpt else []
        if len(starts) > 1:
            cited = _norm(" ".join(lines[c.start_line - 1:c.end_line]))
            wider = [m.start() for m in re.finditer(re.escape(cited), text)] if cited else []
            if len(wider) == 1:
                inner = text.find(excerpt, wider[0])
                starts = [inner] if inner != -1 and inner < wider[0] + len(cited) else starts

        def cover(at):
            end = at + len(excerpt)
            return [(p, g) for s, e, p, g in spans if s < end and at < e]

        if len(starts) == 1:
            hit = cover(starts[0])
            page = hit[0][0]
            if any(p is not page for p, _ in hit):
                out.append(RenderedSourceTarget(**base, status="page_only", page=page["page"],
                                                reason="Excerpt spans a page break; first page shown"))
                continue
            boxes = [g["bbox"] for _, g in hit if "bbox" in g]
            bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                    max(b[3] for b in boxes)] if len(boxes) == len(hit) else None
            out.append(RenderedSourceTarget(**base, page=page["page"], bbox=bbox, status="exact",
                                            dom_targets=[g["id"] for _, g in hit],
                                            reason="" if bbox else "Font widths undeclared; text run highlighted, no box"))
        elif starts:
            out.append(RenderedSourceTarget(**base, status="ambiguous", candidates=[[g["id"] for _, g in cover(s)]
                                                                                    for s in starts],
                                            reason=f"Excerpt occurs {len(starts)} times in the PDF text layer"))
        elif c.start_line in page_map and page_map[c.start_line] <= len(pages):
            out.append(RenderedSourceTarget(**base, status="page_only", page=page_map[c.start_line],
                                            reason="Page has no usable text layer; the page is shown, not the line"))
        else:
            out.append(RenderedSourceTarget(**base, status="unavailable",
                                            reason="Excerpt not found in the PDF text layer"))
    return out


# ---------------------------------------------------------------- public API


def _citations_for(bundle, source_id, atom_manifest=None):
    cites = [c for c in bundle_citations(bundle) if c.source_id == source_id]
    if atom_manifest is not None:
        seen = {(c.start_line, c.end_line, _norm(c.excerpt)) for c in cites}
        for d in atom_manifest.citations:
            c = d.citation
            if c.source_id == source_id and (c.start_line, c.end_line, _norm(c.excerpt)) not in seen:
                cites.append(c)
    return cites


def _finish(bundle, source, mode, lineage, note, raw_sha, media, transforms, nodes, targets, extra=None):
    derivative = {"schema_version": DERIVATIVE_SCHEMA, "source_id": source.source_id, "render_mode": mode,
                  "nodes": nodes, **(extra or {})}
    target_dump = [t.model_dump(mode="json") for t in targets]
    manifest = SourceRenderManifest(
        schema_version=RENDER_MANIFEST_SCHEMA, source_id=source.source_id, bundle_hash=bundle.bundle_hash,
        frozen_sha256=source.sha256, raw_sha256=raw_sha, raw_media_type=media, render_mode=mode, lineage=lineage,
        fidelity_note=note, derivative_sha256=digest(derivative), mapping_sha256=digest(target_dump),
        renderer=dict(RENDERER), transforms=transforms, targets=targets)
    return RenderedSourceAsset(validate_render_manifest(bundle, manifest), derivative).verify()


def render_frozen_text(bundle, source_id, atom_manifest=None, limits=RenderLimits()):
    """Readable line-faithful rendering of the frozen text when no original bytes were supplied."""
    source = next((s for s in bundle.sources if s.source_id == source_id), None)
    if source is None:
        raise ValueError("unknown source")
    if len(source.text.encode()) > limits.max_bytes:
        raise RenderLimitExceeded("source exceeds the declared byte bound")
    budget, transforms = _Budget(limits), []
    nodes, line_nodes, cells = _md_line_tree(source.text, budget, transforms)
    targets = _line_targets(source, _citations_for(bundle, source_id, atom_manifest), line_nodes, cells, None)
    return _finish(bundle, source, "normalized_snapshot", "raw_unavailable",
                   "Readable rendering of the normalized frozen text. No original document bytes were supplied, so "
                   "original layout fidelity is not claimed.", None, None, transforms, nodes, targets)


def prepare_render_asset(bundle, source_id, original_asset=None, atom_manifest=None, limits=RenderLimits()):
    """Render one bundle source from caller-supplied eligible original bytes (or its frozen text)."""
    if original_asset is None:
        return render_frozen_text(bundle, source_id, atom_manifest, limits)
    source = next((s for s in bundle.sources if s.source_id == source_id), None)
    if source is None:
        raise ValueError("unknown source")
    data = original_asset.data
    if len(data) > limits.max_bytes:
        raise RenderLimitExceeded("original exceeds the declared byte bound")
    raw_sha = hashlib.sha256(data).hexdigest()
    if original_asset.declared_sha256 and original_asset.declared_sha256 != raw_sha:
        raise ValueError("original bytes do not match their declared SHA-256")
    matches = data == source.text.encode("utf-8")
    lineage = "raw_matches_frozen" if matches else (
        "raw_derived_text" if original_asset.frozen_derived else "raw_differs_from_frozen")
    media = original_asset.media_type.split(";")[0].strip().lower()
    budget, transforms = _Budget(limits), []
    citations = _citations_for(bundle, source_id, atom_manifest)
    if media in ("text/markdown", "text/plain", "text/x-markdown"):
        if not matches:
            # Line numbers of the frozen text are not line numbers of a different original.
            transforms.append(f"original {media} differs from the frozen text; the frozen text is rendered")
            nodes, line_nodes, cells = _md_line_tree(source.text, budget, transforms)
            targets = _line_targets(source, citations, line_nodes, cells, raw_sha)
            return _finish(bundle, source, "normalized_snapshot", lineage,
                           "Readable rendering of the normalized frozen text; the supplied original differs, so its "
                           "own layout is not shown.", raw_sha, media, transforms, nodes, targets)
        nodes, line_nodes, cells = _md_line_tree(source.text, budget, transforms)
        targets = _line_targets(source, citations, line_nodes, cells, raw_sha)
        return _finish(bundle, source, "faithful_markdown", lineage,
                       "The original Markdown bytes are identical to the frozen text and rendered offline; tables are "
                       "shown as tables only where their structure is proven.", raw_sha, media, transforms, nodes,
                       targets)
    if media in ("text/html", "application/xhtml+xml"):
        nodes = _html_tree(data, budget, transforms)
        targets = _html_targets(source, citations, nodes, raw_sha)
        return _finish(bundle, source, "faithful_html", lineage,
                       "Sanitized offline rendering of the original HTML: scripts, styles, forms, embeds, images and "
                       "remote references removed; structure and text preserved.", raw_sha, media, transforms, nodes,
                       targets)
    if media == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("original is not a PDF")
        pages = _pdf_tree(data, budget, transforms)
        targets = _pdf_targets(source, citations, pages, raw_sha, dict(original_asset.page_map))
        return _finish(bundle, source, "pdf_text_layer", lineage,
                       "PDF text layer placed at its original page coordinates. Images, vector graphics and embedded "
                       "fonts are not rendered; scanned pages without a text layer cannot be highlighted.",
                       raw_sha, media, transforms, pages, targets, {"pages": len(pages)})
    transforms.append(f"original media type {media} is not renderable offline; the frozen text is rendered")
    nodes, line_nodes, cells = _md_line_tree(source.text, budget, transforms)
    targets = _line_targets(source, citations, line_nodes, cells, raw_sha)
    return _finish(bundle, source, "normalized_snapshot", lineage,
                   f"The original ({media}) cannot be rendered offline; this is the normalized frozen text.",
                   raw_sha, media, transforms, nodes, targets)


def resolve_render_target(manifest, target_id):
    """The rendered target for one id, or a ValueError naming why it cannot be opened."""
    target = next((t for t in manifest.targets if t.target_id == target_id), None)
    if target is None:
        raise ValueError("target not in this rendered source")
    return target


def check_derivative(derivative):
    """Structural allowlist check of a derivative tree; refuses unknown tags or attributes."""
    def visit(node):
        if node.get("tag") not in ALLOWED_TAGS:
            raise ValueError("derivative contains a tag outside the allowlist")
        attrs = node.get("attrs", {})
        if set(attrs) - set(SAFE_ATTRS) or any(not re.match(SAFE_ATTRS[k], str(v)) for k, v in attrs.items()):
            raise ValueError("derivative contains an unsafe attribute")
        allowed = {"id", "tag", "text", "children", "attrs", "line", "page", "width", "height", "x", "y", "size",
                   "bbox", "text_layer"}
        if set(node) - allowed:
            raise ValueError("derivative node has unknown fields")
        for child in node.get("children", []):
            visit(child)
    if derivative.get("schema_version") != DERIVATIVE_SCHEMA:
        raise ValueError("unknown derivative schema")
    for n in derivative.get("nodes", []):
        visit(n)
    return derivative
