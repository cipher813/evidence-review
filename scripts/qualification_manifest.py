"""Version-specific qualification manifest: what was built, tested and installed, by content hash.

Run after `bash scripts/check.sh`. It reads artifacts/package-verification.json and
artifacts/coverage.json and writes docs/qualification/<version>.json. The manifest
names the source tree hash, not the commit, because a commit cannot contain its own
hash; the qualified commit is whichever one carries that tree under src/.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def installed_files(root):
    """The same per-file map a consumer computes from the installed package (no caches)."""
    base = root / "src"
    return {p.relative_to(base).as_posix(): sha256(p.read_bytes())
            for p in sorted((base / "evidence_review").rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def build_manifest(root, verification, coverage, tests_passed, source_tree):
    files = installed_files(root)
    return {
        "schema_version": "evidence-review-qualification/v1",
        "package_version": verification["version"],
        "source_tree_sha1": source_tree,
        "verified_source_commit": verification["source_sha"],
        "wheel": verification["wheel"],
        "wheel_sha256": verification["sha256"],
        "reproducible_wheel": verification["reproducible"],
        "clean_noneditable_install": verification["clean_install"],
        "installed_files": files,
        "installed_content_sha256": sha256(canonical(files)),
        "schemas": {p.name: sha256(p.read_bytes()) for p in sorted((root / "schemas").glob("*.json"))},
        "tests_passed": tests_passed,
        "coverage_percent": round(coverage["totals"]["percent_covered"], 2),
        "verified_by_automation": [
            "contracts, atom/render sidecars and capability negotiation",
            "offline rendering of Markdown, HTML and PDF fixtures with exact, page-only and ambiguous targets",
            "three-pane atomic source-check layout in Chromium at 1400x900 and 640x400 (200% zoom)",
            "zero outbound requests and no executable markup from hostile HTML",
            "late hook results fenced and their failures recorded as sanitized events",
            "atom targets and calculation references bound to their own occurrence evidence; exact rendered "
            "targets require frozen-to-original context correspondence (synthetic adversarial fixtures)",
            "rendered-source navigation fenced in Chromium for both response orders, stale failures and task changes",
            "Markdown parser progress on literal hash lines within tight work and node budgets",
            "one check control per assigned atom in atomic mode, with a caller-defined task noun",
            "assigned atom rows and frozen check controls bound both ways for quantities and facts; omitted, wrong "
            "or unknown control ids refused before serving; bound check submits and reloads in Chromium",
            "occurrence-bound prepared calculations shown and navigable in atomic rows beside the unchanged "
            "original candidate evidence, never checking a row (synthetic fixtures, Chromium)",
            "startup with denied tab storage (accessor, getItem, setItem): launch fragment scrubbed, token kept in "
            "memory, navigation, check, save and submit work, reload without a token shows safe recovery (Chromium)",
            "occurrence-bound partial prepared inputs (direct and nested) shown and navigable in atomic rows beside "
            "the original inputs, which keep their own unresolved status, never checking a row (synthetic, Chromium)",
            "one click on an 80-row table keeps the column header, status and a verbatim cited-cell context (column, "
            "row label, units, caption, footnote) in the viewport with the highlighted cell (Chromium, 1400x900)",
            "caller render manifests refused unless every exact target is the derivative's own proven mapping "
            "(missing node, unrelated node, same text in another row, wrong PDF page or box, absent page)",
            "a missing exact node in a served rendering is shown as unavailable, never as an exact highlight (Chromium)",
            "distinct accessible names for each atom row's expansion control, checkbox and source link (Chromium)",
            "allowlisted local HTML styling kept via CSSOM under the unchanged CSP; header scope inferred only where "
            "table structure proves it (synthetic, Chromium)",
            "operand metric, entity, period, unit and tolerance shown in expanded calculation and prepared-input "
            "rows (synthetic, Chromium)",
            "author's declared tolerance shown beside the numeric tolerance and never parsed or used in "
            "recomputation; absent metric, declared tolerance and support declaration keep sealed bundle hashes",
            "author's support declaration shown in atom rows, the calculation card and the evidence pane as the "
            "author's, never checking a row, counting as a citation or changing evidence state or the verified-"
            "verdict gate (synthetic, Chromium)",
            "exact line targets for normalized_snapshot and faithful_markdown proven by re-deriving the canonical "
            "derivative from frozen text; rehashed or relabelled derivatives refused at the validator, open_review "
            "and per request (synthetic adversarial fixtures)",
            "table header context from an occupied-cell grid honouring rowspan/colspan, explicit headers and scope, "
            "nested tables isolated, ambiguous associations shown as unavailable in visible and accessible text "
            "(Chromium)",
            "explicitly associated headers classified as row or column by their declared scope (row/rowgroup/col/"
            "colgroup) before geometry (Chromium)",
            "citation ranges spanning blank lines map to exact targets over their non-blank lines; all-blank ranges "
            "unavailable; excerpt must occur in the range (synthetic, Chromium)",
            "ambiguous numbers with fewer than two located candidates shown as unavailable with a reason instead of "
            "refusing the bundle; ambiguous never shown as located (synthetic shaped like real Primer bundles)",
            "answer-annotation/v1: reviewer-selected answer ranges in Unicode code points bound to field and document "
            "digests, the chosen occurrence of repeated text kept; unknown, context, stale, out-of-bounds or mismatched "
            "ranges refused; draft, restart, stale tab, amendment and export preserve annotations (synthetic, Chromium)",
            "keyboard-only answer annotation: answer field, exact text and occurrence picker give the same code-point "
            "range as a mouse selection, across astral and combining characters (Chromium)",
            "reviewer-calculation-worksheet/v1: reviewer-authored formula and operands with source passages or "
            "unavailable reasons, recomputed by the bounded Decimal evaluator with explicit calculation errors; unsafe "
            "or non-finite input refused; supplied formulas and support never changed; draft, restart, revision and "
            "export preserve it (synthetic, Chromium)",
            "worksheets linked to answer annotations by annotation_id; a dangling link is refused on save and submit, "
            "so a linked annotation cannot be removed; started from an annotation card (synthetic, Chromium)",
            "answers, submissions and exports without annotations or worksheets keep their earlier bytes and hashes",
            "reviewer arithmetic pinned to decimal-v1 (prec=28, ROUND_HALF_EVEN, Emin/Emax -/+999999, traps "
            "InvalidOperation, DivisionByZero, Overflow) whatever the caller's decimal context; non-finite and "
            "out-of-range worksheet results are calculation errors; 0.6.0 computations reproduced digit for digit",
            "export replays each sealed worksheet and keeps its stored computation byte for byte or refuses it naming "
            "the worksheet, never recomputing in place (synthetic 0.6.0 submission fixture)",
            "ambiguous candidates are distinct identities: repeated atom candidate_target_ids and repeated rendered "
            "target candidates refused by model, JSON and schema (uniqueItems); builders degrade repeat-only "
            "locations to unavailable, never located or exact; the view never shows one target twice (synthetic)",
            "answer annotations bind original answer text only: endpoints inside inserted calculation cards, "
            "diagnostics or controls refused; a crossing selection accepted only over one contiguous run of the "
            "field whose text equals the frozen substring (Chromium)",
            "keyboard-only annotation through radio groups for answer field, occurrence, disposition and materiality, "
            "reached by Tab, with the same code-point range as a mouse selection (Chromium)",
            "repeated numeric citations ([A,B,A], [A,A,B], [A,B,A,B]) on ambiguous and multi-citation cited spans build a "
            "whole atom manifest with exactly the distinct candidates in first-seen order; repeat-only stays unavailable; "
            "frozen bundle bytes unchanged; caller manifests that repeat a candidate still refused (synthetic)",
            "keyboard-only annotation test clears text with the portable ControlOrMeta+a (Linux Chromium in this record)",
        ],
        "unverified": [
            "human usability and reviewer effort (no pilot run)",
            "assistive technology with a real screen reader",
            "real-corpus HTML/PDF originals (only synthetic fixtures were rendered)",
            "hosted CI and release publishing for this version",
        ],
    }


def verify_manifest(root, manifest):
    """Refuse a manifest whose recorded content no longer matches this tree."""
    files = installed_files(root)
    if manifest["installed_files"] != files:
        changed = sorted(set(files) ^ set(manifest["installed_files"]) |
                         {k for k in files if manifest["installed_files"].get(k) != files[k]})
        raise ValueError("qualified content differs from this tree: " + ", ".join(changed))
    if manifest["installed_content_sha256"] != sha256(canonical(files)):
        raise ValueError("qualification content digest is inconsistent")
    schemas = {p.name: sha256(p.read_bytes()) for p in sorted((root / "schemas").glob("*.json"))}
    if manifest["schemas"] != schemas:
        raise ValueError("qualified schemas differ from this tree")
    return True


def main():
    verification = json.loads((ROOT / "artifacts/package-verification.json").read_text())
    coverage = json.loads((ROOT / "artifacts/coverage.json").read_text())
    tests_passed = int(sys.argv[1])
    tree = subprocess.run(["git", "rev-parse", f"{verification['source_sha']}:src"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    manifest = build_manifest(ROOT, verification, coverage, tests_passed, tree)
    out = ROOT / "docs/qualification" / f"{manifest['package_version']}.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
