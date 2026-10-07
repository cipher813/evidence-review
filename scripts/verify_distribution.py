"""Reproducible wheel, isolated install and honest source-scope checks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import tempfile
import tomllib
import urllib.request
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
    from evidence_review.atomic_evidence import AtomEvidenceManifest, SourceRenderManifest

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
            (AtomEvidenceManifest, "atom-evidence-v1.json"),
            (SourceRenderManifest, "source-render-manifest-v1.json"),
        ):
            schema = json.loads(archive.read("evidence_review/schemas/" + name))
            if schema != cls.model_json_schema() or schema != json.loads(
                (root / "schemas" / name).read_text()
            ):
                raise ValueError("schema drift")


def requirement_hashes(text):
    """Map each exported requirement to its allowed SHA-256 set."""
    out = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if match:
            current = (canonical(match[1]), match[2])
            out[current] = set()
        for value in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            if current is None:
                raise ValueError("hash without requirement")
            out[current].add(value)
    if not out or any(not hashes for hashes in out.values()):
        raise ValueError("runtime requirements must all be hash-locked")
    return out


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_wheels(lock):
    data = tomllib.loads(lock)
    return {
        (canonical(p["name"]), p["version"]): p.get("wheels", [])
        for p in data["package"]
        if "version" in p
    }


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def stage_wheelhouse(lock, requirements, dest, download=fetch, tags=None):
    """Download every locked runtime wheel while networking is explicit.

    Each byte stream must match both the lock and the exported requirement
    hash before it enters the wheelhouse, so a later offline install depends
    on nothing but these files.
    """
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    ranked = {tag: rank for rank, tag in enumerate(tags or sys_tags())}
    wheels = locked_wheels(lock)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = []
    for (name, version), allowed in sorted(requirement_hashes(requirements).items()):
        candidates = []
        for wheel in wheels.get((name, version), []):
            filename = wheel["url"].rsplit("/", 1)[-1]
            supported = [ranked[t] for t in parse_wheel_filename(filename)[3] if t in ranked]
            if supported:
                candidates.append((min(supported), filename, wheel))
        if not candidates:
            raise ValueError(f"no locked compatible wheel: {name}=={version}")
        _, filename, wheel = min(candidates, key=lambda c: c[0])
        expected = wheel["hash"].removeprefix("sha256:")
        if expected not in allowed:
            raise ValueError(f"lock and requirements disagree: {name}")
        data = download(wheel["url"])
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"hash mismatch: {filename}")
        (dest / filename).write_bytes(data)
        manifest.append({"name": name, "version": version, "file": filename, "sha256": expected})
    return manifest


def verify_wheelhouse(requirements, dest):
    """Refuse an incomplete or altered wheelhouse before installation."""
    from packaging.utils import parse_wheel_filename

    present = {}
    for path in dest.glob("*.whl"):
        name, version = parse_wheel_filename(path.name)[:2]
        present.setdefault((canonical(name), str(version)), []).append(path)
    for key, allowed in requirement_hashes(requirements).items():
        files = present.pop(key, [])
        if len(files) != 1:
            raise ValueError(f"incomplete wheelhouse: {key[0]}=={key[1]}")
        if digest(files[0]) not in allowed:
            raise ValueError(f"hash mismatch: {files[0].name}")
    if present:
        raise ValueError("undeclared wheel in wheelhouse")


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


def reproducible_wheel(root, out, builder=None):
    builder = builder or build
    first = builder(root, out / "first")
    second = builder(root, out / "second")
    if digest(first) != digest(second):
        raise ValueError("wheel is not reproducible")
    return first


def main():
    artifact = ROOT / "artifacts"
    artifact.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as task_tmp:
        task_tmp = Path(task_tmp)
        first = reproducible_wheel(ROOT, task_tmp)
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
        wheelhouse = task_tmp / "wheelhouse"
        requirements = runtime.read_text()
        manifest = stage_wheelhouse(
            (ROOT / "uv.lock").read_text(), requirements, wheelhouse
        )
        verify_wheelhouse(requirements, wheelhouse)
        (artifact / "wheelhouse.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # A fresh cache proves installation depends only on the verified wheelhouse.
        offline = dict(os.environ, UV_CACHE_DIR=str(task_tmp / "empty-cache"))
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--require-hashes",
                "--python",
                str(python),
                "-r",
                str(runtime),
            ],
            check=True,
            env=offline,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-index",
                "--no-deps",
                "--python",
                str(python),
                str(first),
            ],
            check=True,
            env=offline,
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
