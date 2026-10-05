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
evidence-review inspect --bundle bundle.json   # validate and count evidence
evidence-review serve --bundle bundle.json --state-dir review-data
evidence-review export --state-dir review-data --task <bundle-id> --revision 1
```

Enter your assessor name once. Click a report number or claim to see its cited lines with surrounding paragraph, table header and table notes; calculations show the formula, each input with its own evidence, the Decimal recomputation and whether every input is cited. Unresolved numbers are marked with `?`, dates and identifiers with `#`. Open the full frozen source or search it (press `/`), then select its first and last line to attach a passage. Choose support and coverage explicitly; record defects and materiality when applicable. Submit after reviewing the full report. Later submissions require an amendment reason.

Each edit saves on input and displays Saving until acknowledgement. Required boolean decisions accept explicit Yes or No; completion acknowledgements require Yes. Local save status and caller continuation/backup status are shown on separate lines. If another tab saved first, nothing is overwritten: the app lists the differing answers and you choose to load the saved version or keep this tab's answers as a new revision.

The access URL is a local session credential. Protect it and the state directory, which contains hashes, assessor identity, answers and immutable revisions. Back up state through your consumer; the generic app does not push Git.

## How do I verify it?

```sh
uv sync --frozen
uv run --frozen playwright install chromium
UV_CACHE_DIR="$(mktemp -d)" bash scripts/check.sh
```

A healthy result is a green full suite, a passing whole-source coverage floor, identical wheels from two builds and a clean isolated install from a hash-verified wheelhouse, with no prior package cache. CI additionally audits locked runtime dependencies and preserves verification artifacts. Python coverage includes all package and Python delivery-script files; JavaScript is exercised through real Chromium tests and is not included in that percentage.

CI publishes measured badge JSON only after a successful main run; absent measurements display unknown. Main-only publication creates an immutable versioned wheel, checksums, package verification record and generated changelog in a GitHub release. Different wheel content requires a version bump. No PyPI release is implied.

Tests cover two bundle shapes, source/hash/locator checks, calculations, replay, stale tabs, revisions, hooks and loopback authorization. Browser timings are synthetic tests, not a human usability study. Numeric extraction is deterministic; missing or ambiguous evidence remains visible. Source-table header discovery is heuristic; full text and footnotes remain available.

## Where is the rest?

- [SPEC.md](SPEC.md): contracts and intended behavior.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): implementation design.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): installation, compatibility, upgrade, recovery, retention and blinding.
- [BUILD_STATUS.md](BUILD_STATUS.md) and [HARDENING.md](HARDENING.md): verification and hardening register.
- [CONTRIBUTING.md](CONTRIBUTING.md): changes and verification.
- [SECURITY.md](SECURITY.md): private disclosure and response targets.
- [GitHub releases](https://github.com/nousergon/evidence-review/releases): tested distributions and generated changelogs.
- [LICENSE](LICENSE): MIT, maintained by @cipher813.

## Embedded use

```python
from evidence_review import FileStore, Hooks, open_review, validate_bundle

bundle = validate_bundle(data)
hooks = Hooks(on_submission=backup, reconcile=check_receipt, next_bundle=next_task, timeout=30)
with open_review(bundle, FileStore("review-data"), hooks, blind_markers=identity_terms) as review:
    print(review.url)  # local session credential; keep private
    review.thread.join()  # serve until interrupted
```

Callbacks receive recursively immutable submissions. Return status (pending, succeeded, failed, unknown), a durable identifier for success and a reason. Revisions use on_revision. A crash leaves continuation pending and a callback exceeding `timeout` is recorded as unknown; `reconcile` must confirm the caller's own receipt. Side effects are never automatically retried. Optional next_bundle supplies the caller-owned queue after successful submission. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for installation pins, schema compatibility, upgrades, recovery, retention and blinding.
