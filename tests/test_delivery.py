"""Protect source coverage and published artifact/badge honesty."""

import json
from pathlib import Path
import tomllib
import pytest
from scripts.verify_distribution import verify_scope
from scripts.badges import payloads
from evidence_review.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_coverage_scope_cannot_hide_unimported_sources(tmp_path):
    (tmp_path / "src/evidence_review").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src/evidence_review/new.py").write_text("raise RuntimeError")
    coverage = {"files": {}, "totals": {"num_statements": 1}}
    with pytest.raises(ValueError, match="denominator"):
        verify_scope(tmp_path, coverage)
    coverage["files"] = {"src/evidence_review/new.py": {}}
    verify_scope(tmp_path, coverage)
    coverage["totals"]["num_statements"] = 0
    with pytest.raises(ValueError, match="evidence"):
        verify_scope(tmp_path, coverage)


def test_config_preserves_whole_source_floor_and_no_omits():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]
    assert cfg["run"]["source"] == ["src/evidence_review", "scripts"]
    assert not cfg["run"].get("omit") and not cfg["report"].get("omit")
    assert cfg["report"]["fail_under"] >= 92.0


def test_badges_reject_missing_measurement_and_bind_source():
    package = {
        "reproducible": True,
        "clean_install": True,
        "source_sha": "a" * 40,
        "license": "MIT",
        "python": ">=3.12,<3.13",
    }
    coverage = {"totals": {"num_statements": 100, "percent_covered": 91.23}}
    out = payloads(coverage, package)
    assert out["coverage.json"]["message"] == "91.23%"
    assert all(v["sourceSha"] == package["source_sha"] for v in out.values())
    package["clean_install"] = False
    with pytest.raises(ValueError, match="incomplete"):
        payloads(coverage, package)
    package["clean_install"] = True
    coverage["totals"]["num_statements"] = 0
    with pytest.raises(ValueError, match="missing"):
        payloads(coverage, package)


def test_cli_rejects_symlink_bundle_before_serving(tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(bundle)
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--bundle", str(link), "--state-dir", str(tmp_path / "state")])
    assert exc.value.code == 2


def test_wheel_integrity_rejects_changed_license_or_schema(tmp_path):
    import shutil
    import zipfile
    from scripts.verify_distribution import verify_wheel

    root = tmp_path / "repo"
    shutil.copytree(ROOT / "src", root / "src")
    shutil.copytree(ROOT / "schemas", root / "schemas")
    shutil.copy2(ROOT / "LICENSE", root / "LICENSE")
    wheel = tmp_path / "test.whl"

    def pack(license_body):
        with zipfile.ZipFile(wheel, "w") as z:
            for path in (root / "src/evidence_review").rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    z.write(path, path.relative_to(root / "src").as_posix())
            z.writestr("evidence_review-0.1.0.dist-info/licenses/LICENSE", license_body)

    pack(b"wrong license")
    with pytest.raises(ValueError, match="license"):
        verify_wheel(root, wheel)
    pack((root / "LICENSE").read_bytes())
    verify_wheel(root, wheel)
    (root / "schemas/review-bundle-v1.json").write_text("{}")
    with pytest.raises(ValueError, match="schema drift"):
        verify_wheel(root, wheel)


@pytest.mark.parametrize(
    "failure",
    [
        "HTTP 503",
        "HTTP 401",
        "HTTP 404",
        "existing",
        "draft",
        "upload-failure",
        "stale",
        "wrong-tag",
    ],
)
def test_publisher_only_creates_on_true_absence(tmp_path, failure):
    import hashlib
    import os
    import subprocess

    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    wheel = artifact / "evidence_review-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"tested distribution")
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    record = {
        "source_sha": "a" * 40,
        "reproducible": True,
        "clean_install": True,
        "version": "0.1.0",
        "wheel": wheel.name,
        "sha256": checksum,
    }
    (artifact / "package-verification.json").write_text(json.dumps(record))
    (artifact / "SHA256SUMS").write_text(f"{checksum}  {wheel.name}\n")
    for name in ("coverage", "license", "python"):
        (artifact / f"badge-{name}.json").write_text(
            json.dumps({"sourceSha": record["source_sha"]})
        )
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "gh"
    fake.write_text(
        "#!"
        + __import__("sys").executable
        + "\n"
        + """
import os,sys,json,shutil
from pathlib import Path
a=sys.argv[1:]
with Path("calls").open("a") as f: f.write(json.dumps(a)+"\\n")
mode=os.environ["TEST_FAILURE"]
sha=os.environ["GITHUB_SHA"]
if a[0]=="api" and "/releases/tags/" in a[1]:
    if mode in ("existing","stale"): print(json.dumps({"draft":False,"target_commitish":sha,"body":"Changes"}))
    elif mode in ("draft","upload-failure") or Path("created").exists(): print(json.dumps({"draft":True,"target_commitish":sha,"body":"Changes"}))
    else:
        code="HTTP 404" if mode=="wrong-tag" else mode
        print("gh: failed ("+code+")",file=sys.stderr); sys.exit(1)
elif a[0]=="api" and "/commits/v" in a[1]:
    print("c"*40 if mode=="wrong-tag" else sha)
elif a[:2]==["release","create"]:
    Path("created").touch()
elif a[:2]==["release","upload"] and mode=="upload-failure":
    print("interrupted upload",file=sys.stderr); sys.exit(1)
elif a[:2]==["release","download"]:
    Path("artifacts/existing").mkdir(exist_ok=True)
    for p in Path("artifacts").iterdir():
        if p.suffix==".whl" or p.name in ("SHA256SUMS","package-verification.json","CHANGELOG.md"):
            shutil.copy(p,Path("artifacts/existing")/p.name)
    Path("artifacts/existing/CHANGELOG.md").write_text("Changes")
elif a[0]=="api" and "/git/ref/heads/main" in a[1]:
    print("c"*40 if mode=="stale" else sha)
else: print("b"*40)
"""
    )
    fake.chmod(0o755)
    task_vars = dict(
        os.environ,
        PATH=str(binary) + os.pathsep + os.environ["PATH"],
        GH_TOKEN="fake-test-only",
        GITHUB_REPOSITORY="test/tool",
        GITHUB_SHA=record["source_sha"],
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/heads/main",
        TEST_FAILURE=failure,
    )
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/publish.sh")],
        cwd=tmp_path,
        env=task_vars,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    creates = [call for call in calls if call[:2] == ["release", "create"]]
    if failure in ("HTTP 503", "HTTP 401"):
        assert result.returncode != 0
        assert len(calls) == 1 and not creates
    elif failure in ("upload-failure", "wrong-tag"):
        assert result.returncode != 0
        assert not any(call[:2] == ["release", "edit"] for call in calls)
        assert not creates
    else:
        assert result.returncode == 0, result.stderr
        assert bool(creates) == (failure == "HTTP 404")
        if creates:
            assert "--draft" in creates[0]
        if failure in ("HTTP 404", "draft"):
            upload = next(
                n for n, c in enumerate(calls) if c[:2] == ["release", "upload"]
            )
            download = next(
                n for n, c in enumerate(calls) if c[:2] == ["release", "download"]
            )
            publish = next(
                n for n, c in enumerate(calls) if c[:2] == ["release", "edit"]
            )
            assert upload < download < publish
            assert {
                "artifacts/coverage.json",
                "artifacts/dependency-audit.json",
                "artifacts/wheelhouse.json",
            } <= set(calls[upload])
        if failure == "stale":
            assert not (artifact / "badge-tree.json").exists()
        else:
            tree = json.loads((artifact / "badge-tree.json").read_text())
            assert {row["path"] for row in tree["tree"]} == {
                "coverage.json",
                "license.json",
                "python.json",
            }
            assert calls[-1][-2:] == ["force=false", "--silent"]


def test_publisher_refuses_non_main_and_unverified_input(tmp_path):
    import subprocess

    result = subprocess.run(
        ["sh", str(ROOT / "scripts/publish.sh")],
        cwd=tmp_path,
        env={
            "PATH": __import__("os").environ["PATH"],
            "GH_TOKEN": "fake",
            "GITHUB_REPOSITORY": "test/tool",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/1/merge",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Only a main push" in result.stderr


def test_workflow_is_pinned_and_prs_cannot_publish():
    import re

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    refs = re.findall(r"uses:\s+([^\s#]+)", workflow)
    assert refs and all(
        re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", ref) for ref in refs
    )
    assert "pull_request_target" not in workflow
    assert "paths:" not in workflow and "schedule:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "needs: quality" in workflow
    assert (
        "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    )
    assert "persist-credentials: false" in workflow
    assert "bash scripts/check.sh" in workflow
    assert "--require-hashes" in workflow


def test_fractional_coverage_floor_is_enforced():
    from coverage.results import should_fail_under

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"][
        "report"
    ]
    assert should_fail_under(90.51, cfg["fail_under"], cfg["precision"])
    assert not should_fail_under(
        cfg["fail_under"] + 0.004, cfg["fail_under"], cfg["precision"]
    )


def test_isolated_distribution_uses_locked_runtime_and_build_hashes():
    script = (ROOT / "scripts/verify_distribution.py").read_text()
    assert '"--require-hashes"' in script and '"build-requirements.txt"' in script
    assert '"--no-deps"' in script and '"--frozen"' in script
    # Installation must not depend on a resolver cache or a package index.
    assert script.count('"--no-index"') == 2 and "UV_CACHE_DIR" in script
    assert "hatchling==1.29.0" in (ROOT / "build-requirements.txt").read_text()


def test_build_constraints_match_project_backend_requirement():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert (ROOT / "build-requirements.in").read_text().splitlines() == cfg["build-system"]["requires"]
    assert "uv lock --check\n" in (ROOT / "scripts/check.sh").read_text()


def _locked_pair(tmp_path):
    import hashlib

    good = b"synthetic wheel bytes"
    sha = hashlib.sha256(good).hexdigest()
    lock = f"""
version = 1
[[package]]
name = "demo-dep"
version = "1.0"
wheels = [
  {{ url = "https://files.invalid/demo_dep-1.0-py3-none-any.whl", hash = "sha256:{sha}" }},
  {{ url = "https://files.invalid/demo_dep-1.0-cp99-cp99-plan9_x.whl", hash = "sha256:{'0' * 64}" }},
]
"""
    requirements = f"demo-dep==1.0 \\\n    --hash=sha256:{sha} \\\n    --hash=sha256:{'0' * 64}\n"
    return lock, requirements, good, sha


def test_wheelhouse_is_staged_from_lock_and_verified(tmp_path):
    from packaging.tags import Tag
    from scripts.verify_distribution import stage_wheelhouse, verify_wheelhouse

    lock, requirements, good, sha = _locked_pair(tmp_path)
    fetched = []
    tags = [Tag("py3", "none", "any")]

    def download(url):
        fetched.append(url)
        return good

    house = tmp_path / "house"
    manifest = stage_wheelhouse(lock, requirements, house, download, tags)
    assert manifest == [
        {"name": "demo-dep", "version": "1.0", "file": "demo_dep-1.0-py3-none-any.whl", "sha256": sha}
    ]
    assert fetched == ["https://files.invalid/demo_dep-1.0-py3-none-any.whl"]
    verify_wheelhouse(requirements, house)


@pytest.mark.parametrize("fault", ["hash", "missing", "incomplete", "undeclared", "unlocked", "disagree"])
def test_wheelhouse_faults_fail_before_install(tmp_path, fault):
    from packaging.tags import Tag
    from scripts.verify_distribution import stage_wheelhouse, verify_wheelhouse

    lock, requirements, good, sha = _locked_pair(tmp_path)
    tags = [Tag("py3", "none", "any")]
    house = tmp_path / "house"
    if fault == "hash":
        with pytest.raises(ValueError, match="hash mismatch"):
            stage_wheelhouse(lock, requirements, house, lambda url: b"tampered", tags)
        assert not list(house.glob("*.whl"))
        return
    if fault == "missing":
        with pytest.raises(ValueError, match="no locked compatible wheel"):
            stage_wheelhouse(lock, requirements, house, lambda url: good, [Tag("cp1", "none", "x")])
        return
    if fault == "unlocked":
        with pytest.raises(ValueError, match="hash-locked"):
            stage_wheelhouse(lock, "demo-dep==1.0\n", house, lambda url: good, tags)
        return
    if fault == "disagree":
        other = requirements.replace(sha, "1" * 64)
        with pytest.raises(ValueError, match="disagree"):
            stage_wheelhouse(lock, other, house, lambda url: good, tags)
        return
    stage_wheelhouse(lock, requirements, house, lambda url: good, tags)
    if fault == "incomplete":
        next(house.glob("*.whl")).unlink()
        match = "incomplete"
    else:
        (house / "extra-2.0-py3-none-any.whl").write_bytes(b"x")
        match = "undeclared"
    with pytest.raises(ValueError, match=match):
        verify_wheelhouse(requirements, house)


def test_wheelhouse_rejects_altered_staged_bytes(tmp_path):
    from packaging.tags import Tag
    from scripts.verify_distribution import stage_wheelhouse, verify_wheelhouse

    lock, requirements, good, sha = _locked_pair(tmp_path)
    house = tmp_path / "house"
    stage_wheelhouse(lock, requirements, house, lambda url: good, [Tag("py3", "none", "any")])
    next(house.glob("*.whl")).write_bytes(b"changed after staging")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_wheelhouse(requirements, house)


def test_second_build_mismatch_fails_before_publish(tmp_path):
    from scripts.verify_distribution import reproducible_wheel

    def builder(root, out):
        out.mkdir(parents=True)
        wheel = out / "pkg-1.0-py3-none-any.whl"
        wheel.write_bytes(out.name.encode())
        return wheel

    with pytest.raises(ValueError, match="not reproducible"):
        reproducible_wheel(tmp_path, tmp_path, builder)
