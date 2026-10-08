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
