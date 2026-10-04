# Evidence Review

Local evidence-linked human review for reports, claims and calculations.

## What it does
A reusable Python package and local browser app for inspecting documents against sources and recording human judgments. The tool automates navigation, metadata, autosave and structured exports.

## Current status and running it
Implementation under review; no registry release published. See [SPEC.md](SPEC.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). The runnable demo uses artificial data and requires no account or network.

## Verification
Local verification: 20 tests passed, including actual Chromium. Whole Python package coverage: 91% on Python 3.12/macOS; JavaScript behavior is exercised through browser tests, not included in that percentage.

## Ownership and contributions
Maintained by @cipher813. Open an issue for design/bug feedback; see [SECURITY.md](SECURITY.md) for sensitive reports. Do not attach private documents or credentials.

## License
MIT; see [LICENSE](LICENSE). No distributable release has been published.

## Local use
Requires Python 3.12. Install the tested commit (replace `<commit>` with the reviewed full SHA):

```sh
pip install "evidence-review @ git+https://github.com/cipher813/evidence-review.git@<commit>"
evidence-review demo
```

The demo is artificial and needs no account or network at runtime. For your own validated bundle:

```sh
evidence-review serve --bundle bundle.json --state-dir review-data
```

Enter your assessor name once. Click a report number or claim, open its frozen source, then click the first and last source line to select a passage. Attach it to your judgment. Choose support/coverage explicitly; record defects and materiality when applicable. Answers save locally after acknowledgement. Submit only after reviewing the full report; later submissions require an amendment reason. Local save and caller continuation/backup status are separate.

The access URL is a local session credential. Keep it private. State directories contain report/source hashes, answers, assessor identity and immutable revisions; protect and back them up through your consumer. The generic app does not push Git or call models.

## Embedded use

```python
from evidence_review import validate_bundle, open_review
from evidence_review.store import FileStore
from evidence_review.hooks import Hooks

bundle = validate_bundle(data)
with open_review(bundle, FileStore("review-data"), Hooks(on_submission=your_callback)) as review:
    wait_for_user()
```

Callbacks receive recursively immutable submissions. Return `status` (`pending`, `succeeded`, `failed`), a durable `identifier` for success, and `reason`. Revisions use `on_revision`. A crash during a callback leaves continuation pending: `reconcile` must resolve the caller's idempotent operation; arbitrary side effects are never automatically retried. Optional `next_bundle` supplies the caller-owned queue after successful submission.

## Verification and limits

```sh
uv sync --frozen
uv run playwright install chromium
uv run pytest
uv build
```

Tests exercise actual Chromium, two consumer bundle shapes, source/hash/locator checks, write-ahead replay, optimistic revisions, stale tabs, hook failure and loopback authorization. Schemas ship in the wheel. This release is a small local review tool, not a multi-user hosted platform. Numeric extraction is deterministic, not a semantic evidence linker: missing/ambiguous associations stay visible. Source-table header discovery is heuristic; full text and footnotes remain available. Human judgments remain human; neither arithmetic nor matching excerpts establish support. Administrative workflow tests are not a measured human usability study.
