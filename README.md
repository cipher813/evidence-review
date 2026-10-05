# Evidence Review

[![CI](https://github.com/nousergon/evidence-review/actions/workflows/ci.yml/badge.svg)](https://github.com/nousergon/evidence-review/actions/workflows/ci.yml)
![Python coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fnousergon%2Fevidence-review%2Fbadges%2Fcoverage.json)
![License](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fnousergon%2Fevidence-review%2Fbadges%2Flicense.json)
![Python](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fnousergon%2Fevidence-review%2Fbadges%2Fpython.json)

## What is this?

A reusable Python package and local browser app for checking reports, claims and calculations against frozen sources and recording human judgments. It automates evidence navigation, autosave, revisions and structured exports. Review data stays with the caller; the app does not call models or upload documents.

## Why does it exist?

Report assessment requires both a readable report and its underlying evidence. Evidence Review places them together and records explicit decisions with source passages and immutable revisions. Arithmetic and matching excerpts assist review; they do not establish semantic support or replace human judgment.

## How do I run it?

Requires Python 3.12. The browser app works on macOS and Linux. Install the tested wheel from a [GitHub release](https://github.com/nousergon/evidence-review/releases), verifying its SHA256SUMS first, or install an exact reviewed source commit:

```sh
pip install "evidence-review @ git+https://github.com/nousergon/evidence-review.git@<full-reviewed-commit>"
evidence-review demo
```

The demo uses artificial data and needs no account or network at runtime. For your own validated bundle:

```sh
evidence-review serve --bundle bundle.json --state-dir review-data
```

Enter your assessor name once. Click a report number or claim, open its frozen source, then select its first and last source line to attach a passage. Choose support and coverage explicitly; record defects and materiality when applicable. Submit after reviewing the full report. Later submissions require an amendment reason.

Each text edit saves on input and displays Saving until acknowledgement. Required boolean decisions accept explicit Yes or No; completion acknowledgements require Yes. Local save and caller continuation/backup status are separate.

The access URL is a local session credential. Protect it and the state directory, which contains hashes, assessor identity, answers and immutable revisions. Back up state through your consumer; the generic app does not push Git.

## How do I verify it?

```sh
uv sync --frozen
uv run --frozen playwright install chromium
bash scripts/check.sh
```

A healthy result is a green full suite, a passing whole-source coverage floor, identical wheels from two builds and a clean isolated install. CI additionally audits locked runtime dependencies and preserves verification artifacts. Python coverage includes all package and Python delivery-script files; JavaScript is exercised through real Chromium tests and is not included in that percentage.

CI publishes measured badge JSON only after a successful main run; absent measurements display unknown. Main-only publication creates an immutable versioned wheel, checksums, package verification record and generated changelog in a GitHub release. Different wheel content requires a version bump. No PyPI release is implied.

Tests cover two bundle shapes, source/hash/locator checks, calculations, replay, stale tabs, revisions, hooks and loopback authorization. Browser timings are synthetic tests, not a human usability study. Numeric extraction is deterministic; missing or ambiguous evidence remains visible. Source-table header discovery is heuristic; full text and footnotes remain available.

## Where is the rest?

- [SPEC.md](SPEC.md): contracts and intended behavior.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): implementation design.
- [BUILD_STATUS.md](BUILD_STATUS.md) and [HARDENING.md](HARDENING.md): verification and hardening register.
- [CONTRIBUTING.md](CONTRIBUTING.md): changes and verification.
- [SECURITY.md](SECURITY.md): private disclosure and response targets.
- [GitHub releases](https://github.com/nousergon/evidence-review/releases): tested distributions and generated changelogs.
- [LICENSE](LICENSE): MIT, maintained by @cipher813.

## Embedded use

```python
from evidence_review import validate_bundle, open_review
from evidence_review.store import FileStore
from evidence_review.hooks import Hooks

bundle = validate_bundle(data)
with open_review(bundle, FileStore("review-data"), Hooks(on_submission=your_callback)) as review:
    wait_for_user()
```

Callbacks receive recursively immutable submissions. Return status (pending, succeeded, failed), a durable identifier for success and a reason. Revisions use on_revision. A crash leaves continuation pending: reconcile must resolve the caller's idempotent operation; arbitrary side effects are never automatically retried. Optional next_bundle supplies the caller-owned queue after successful submission.
