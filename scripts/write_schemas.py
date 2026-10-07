"""Regenerate the published JSON schemas from the strict models (root and packaged copies)."""
import json
from pathlib import Path

from evidence_review.atomic_evidence import AtomEvidenceManifest, SourceRenderManifest
from evidence_review.contracts import ReviewBundle, ReviewSubmission

SCHEMAS = {
    "review-bundle-v1.json": ReviewBundle,
    "review-submission-v1.json": ReviewSubmission,
    "atom-evidence-v1.json": AtomEvidenceManifest,
    "source-render-manifest-v1.json": SourceRenderManifest,
}


def main():
    root = Path(__file__).resolve().parents[1]
    for name, model in SCHEMAS.items():
        text = json.dumps(model.model_json_schema(), indent=2) + "\n"
        for folder in (root / "schemas", root / "src" / "evidence_review" / "schemas"):
            (folder / name).write_text(text)


if __name__ == "__main__":
    main()
