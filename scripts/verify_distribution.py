"""Reproducible wheel, isolated install and honest source-scope checks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_scope(root, coverage):
    expected = {
        p.relative_to(root).as_posix()
        for base in ("src/evidence_review", "scripts")
        for p in (root / base).rglob("*.py")
    }
    if set(coverage["files"]) != expected:
        raise ValueError("coverage denominator differs from the complete source set")
    if coverage["totals"]["num_statements"] <= 0:
        raise ValueError("no coverage evidence")


def verify_wheel(root, wheel):
    from evidence_review.contracts import ReviewBundle, ReviewSubmission

    with zipfile.ZipFile(wheel) as archive:
        for path in (root / "src/evidence_review").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                name = path.relative_to(root / "src").as_posix()
                if archive.read(name) != path.read_bytes():
                    raise ValueError("wheel source differs: " + name)
        if not any(
            n.endswith("/licenses/LICENSE")
            and archive.read(n) == (root / "LICENSE").read_bytes()
            for n in archive.namelist()
        ):
            raise ValueError("MIT license missing or changed")
        for cls, name in (
            (ReviewBundle, "review-bundle-v1.json"),
            (ReviewSubmission, "review-submission-v1.json"),
        ):
            schema = json.loads(archive.read("evidence_review/schemas/" + name))
            if schema != cls.model_json_schema() or schema != json.loads(
                (root / "schemas" / name).read_text()
            ):
                raise ValueError("schema drift")


def build(root, out):
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--require-hashes",
            "--build-constraints",
            str(root / "build-requirements.txt"),
            "--out-dir",
            str(out),
        ],
        cwd=root,
        check=True,
    )
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("expected one wheel")
    return wheels[0]


def main():
    artifact = ROOT / "artifacts"
    artifact.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as task_tmp:
        task_tmp = Path(task_tmp)
        first = build(ROOT, task_tmp / "first")
        second = build(ROOT, task_tmp / "second")
        if digest(first) != digest(second):
            raise ValueError("wheel is not reproducible")
        verify_wheel(ROOT, first)
        task_venv = task_tmp / "clean"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(task_venv)], check=True
        )
        python = task_venv / "bin/python"
        runtime = artifact / "runtime-requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(runtime),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--require-hashes",
                "--python",
                str(python),
                "-r",
                str(runtime),
            ],
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(python),
                str(first),
            ],
            check=True,
        )
        smoke = """from importlib.resources import files
from evidence_review import validate_bundle, open_review
from evidence_review.example import example_bundle
from evidence_review.store import FileStore
import tempfile, urllib.request
b=validate_bundle(example_bundle().model_dump(mode="json"))
assert files("evidence_review").joinpath("ui/app.js").is_file()
with open_review(b, FileStore(tempfile.mkdtemp()), launch=False) as handle:
    assert urllib.request.urlopen(handle.url).status == 200
"""
        subprocess.run([str(python), "-I", "-c", smoke], cwd=task_tmp, check=True)
        subprocess.run(
            [str(task_venv / "bin/evidence-review"), "--help"],
            cwd=task_tmp,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        target = artifact / first.name
        target.write_bytes(first.read_bytes())
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        record = {
            "schema_version": "package-verification/v1",
            "source_sha": os.environ.get("GITHUB_SHA")
            or subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "version": meta["version"],
            "license": meta["license"],
            "python": meta["requires-python"],
            "wheel": target.name,
            "sha256": digest(target),
            "reproducible": True,
            "clean_install": True,
        }
        (artifact / "package-verification.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        (artifact / "SHA256SUMS").write_text(f"{digest(target)}  {target.name}\n")


if __name__ == "__main__":
    main()
