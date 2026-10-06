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

On wide screens, the report and claim selection, current judgment, and source evidence occupy independently scrolling panes. Narrow screens stack the panes. Coverage checkboxes sit beside the exact claim text. Selected decisions that still need explanations are distinguished from unanswered items; submission errors link directly to incomplete fields.

Enter your assessor name once. A reported number with one exact source opens its original line in a new tab; adjacent Preview evidence keeps the frozen context local. A derived number opens a compact calculation card with directly linked input values and an optional full preview. Arithmetic results remain unresolved when source inputs are missing. Only explicitly bound per-number evidence is used; whole-claim evidence is a separately labeled action. Unresolved numbers are marked with `?`, dates and identifiers with `#`. Open the full frozen source or search it (press `/`), then select its first and last line to attach a passage. Choose support and coverage explicitly; record defects and materiality when applicable. Submit after reviewing the full report. Later submissions require an amendment reason.

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

Tests cover two bundle shapes, source/hash/locator checks, calculations, replay, stale tabs, revisions, hooks and loopback authorization. Browser timings are synthetic tests, not a human usability study. Numeric extraction is deterministic; missing or ambiguous evidence remains visible. Conservative table parsing renders uniquely aligned headers and rows; ambiguous layouts remain explicitly unparsed with numbered original lines, full text and footnotes available. Caller-supplied workload separates assigned answers from required judgments, optional fields and completion controls. Prepared evidence remains separately labeled and never repairs the answer silently. Optional technical diagnostics distinguish candidate citation/input/arithmetic checks from independent preparation attempts, retain earlier limitations after a later method succeeds, and expose frozen-source inspection. These checks never assign support, defects or materiality.

## Where is the rest?

- [SPEC.md](SPEC.md): contracts and intended behavior.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): implementation design.
- [docs/USABILITY_IMPLEMENTATION.md](docs/USABILITY_IMPLEMENTATION.md): navigation, evidence and workload qualification, with remaining human gates.
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

Callbacks receive recursively immutable submissions. Return status (pending, succeeded, failed, unknown), a durable identifier for success and a reason. Revisions use on_revision. A crash leaves continuation pending and a callback exceeding `timeout` is recorded as unknown; `reconcile` must confirm the caller's own receipt. Side effects are never automatically retried. Concurrent callers, including separate processes sharing a state directory, take a durable claim on each attempt so only one runs the callback, and a late or superseded result cannot overwrite a newer one. This is not exactly-once delivery: a crash or timeout can leave an effect that happened recorded as unknown, so make your callbacks idempotent (for example keyed by bundle and revision). Optional next_bundle supplies the caller-owned queue after successful submission. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for installation pins, schema compatibility, upgrades, recovery, retention and blinding.
