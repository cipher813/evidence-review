"""The committed qualification manifest names exactly the content this tree installs."""
import json
import shutil
from pathlib import Path

import pytest

from evidence_review.contracts import PACKAGE_VERSION
from scripts.qualification_manifest import build_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "docs/qualification" / f"{PACKAGE_VERSION}.json").read_text())


def test_manifest_for_the_current_version_matches_this_tree():
    assert MANIFEST["package_version"] == PACKAGE_VERSION
    assert verify_manifest(ROOT, MANIFEST)
    assert MANIFEST["reproducible_wheel"] and MANIFEST["clean_noneditable_install"]
    assert any("usability" in u for u in MANIFEST["unverified"])


def copy(tmp_path):
    for name in ("src", "schemas"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


@pytest.mark.parametrize("target", ["src/evidence_review/server.py", "src/evidence_review/ui/app.js"])
def test_changed_code_or_served_asset_is_refused(tmp_path, target):
    root = copy(tmp_path)
    (root / target).write_bytes((root / target).read_bytes() + b"\n// changed\n")
    with pytest.raises(ValueError, match="differs from this tree"):
        verify_manifest(root, MANIFEST)


def test_changed_schema_and_forged_digest_are_refused(tmp_path):
    root = copy(tmp_path)
    with pytest.raises(ValueError, match="digest is inconsistent"):
        verify_manifest(root, {**MANIFEST, "installed_content_sha256": "0" * 64})
    schema = root / "schemas/review-bundle-v1.json"
    schema.write_text(schema.read_text().replace("review-bundle", "review-bundlx", 1))
    with pytest.raises(ValueError, match="schemas differ"):
        verify_manifest(root, MANIFEST)


def test_build_manifest_records_the_verification_it_was_given():
    verification = {"version": PACKAGE_VERSION, "source_sha": "c" * 40, "wheel": "w.whl", "sha256": "d" * 64,
                    "reproducible": True, "clean_install": True}
    built = build_manifest(ROOT, verification, {"totals": {"percent_covered": 92.484}}, 1, "e" * 40)
    assert built["wheel_sha256"] == "d" * 64 and built["coverage_percent"] == 92.48
    assert built["installed_content_sha256"] == MANIFEST["installed_content_sha256"]
