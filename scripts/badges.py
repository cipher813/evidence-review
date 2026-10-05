"""Badge payloads from terminal CI measurements only."""

import json
from pathlib import Path


def payloads(coverage, package):
    if not package["reproducible"] or not package["clean_install"]:
        raise ValueError("package verification incomplete")
    totals = coverage["totals"]
    if totals["num_statements"] <= 0:
        raise ValueError("coverage missing")
    pct = totals["percent_covered"]

    def badge(label, message, color):
        return {
            "schemaVersion": 1,
            "label": label,
            "message": message,
            "color": color,
            "sourceSha": package["source_sha"],
        }

    return {
        "coverage.json": badge(
            "Python coverage", f"{pct:.2f}%", "brightgreen" if pct >= 90 else "green"
        ),
        "license.json": badge("license", package["license"], "blue"),
        "python.json": badge("Python", package["python"], "blue"),
    }


def main():
    root = Path("artifacts")
    data = payloads(
        json.loads((root / "coverage.json").read_text()),
        json.loads((root / "package-verification.json").read_text()),
    )
    for name, body in data.items():
        (root / ("badge-" + name)).write_text(json.dumps(body, indent=2) + "\n")


if __name__ == "__main__":
    main()
