from importlib.resources import files
import json
from evidence_review import validate_bundle
from evidence_review.example import example_bundle


def test_distribution_contains_versioned_contracts_and_local_assets():
    root = files("evidence_review")
    for name in ["index.html", "app.js", "styles.css"]:
        assert len(root.joinpath("ui", name).read_bytes()) > 10
    schema = json.loads(root.joinpath("schemas", "review-bundle-v1.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == "review-bundle/v1"
    # Consumer shape includes no claims and no quantitative text, as in an unavailable document.
    raw = example_bundle().model_dump(mode="json")
    raw.update(
        bundle_id="unavailable-document",
        fields=[
            {
                "path": "notice",
                "label": "Status",
                "text": "Document unavailable",
                "claim_ids": [],
            }
        ],
        claims=[],
        spans=[],
    )
    assert not validate_bundle(raw).spans
