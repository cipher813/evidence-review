# Evidence Review implementation plan

> For agentic workers: use superpowers:executing-plans in the separate development session. Do not dispatch additional agents without authorization.

**Goal:** ship a reusable local review package and browser app before application-specific integration.
**Architecture:** versioned bundle/submission contracts, generic filesystem store, callback hooks and one shared server/UI. No application imports or evaluation/provider logic.
**Spec:** SPEC.md. **Stack:** Python 3.12, Pydantic with an exact tested lockfile pin, standard-library HTTP, vanilla browser assets.
**Status:** historical bootstrap plan. Implemented and released as described in [BUILD_STATUS.md](BUILD_STATUS.md); current contracts are in [SPEC.md](SPEC.md).

## Constraints
Public files must have an external reader's purpose. Synthetic examples only. No real corpus, customer data, private prompts/rubrics, model routes or results. License: MIT, approved by the owner. Include LICENSE in source and wheel distributions and declare MIT in package metadata. No cloud deployment, account creation, paid services or model calls. Runtime dependencies and browser dev dependencies, if added, use tested pins and a lockfile. Keep repository checks minimal and substantive; no workflows at bootstrap, no schedules or cloud workers.

## Tasks
1. Contracts and synthetic evidence. Create src/evidence_review/contracts.py, evidence.py; schemas/review-bundle-v1.json and review-submission-v1.json; tests/test_contracts.py/test_evidence.py. Define the exact SPEC interfaces. validate_bundle rejects missing source hashes, unknown IDs and stale data. Enumerate numeric spans across all report fields, retaining repeated offsets and explicit unresolved associations. Recompute provided formulas with Decimal and a safe evaluator, never eval. Example synthetic margin moves 22.8% to 24.6%=180bps with both operands and line contexts. Fixtures include prose-only number, currency/negative/range/date/identifier, no formula, missing input/source, wrong period/unit and table footnote. Run red/green tests and commit.
2. Durable store. Create store.py, events.py, tests/test_store.py. Filesystem storage implements append_event/load_task/save_snapshot/save_submission, revision checks and idempotency. Write-ahead events precede atomic/fsync snapshot; crash replay has exactly one accepted answer. Lock only transactions, never human wait. Preserve submitted revisions with reasons, reject stale bundle/rubric. Test two tabs, duplicate requests, interrupted snapshot/submission, malformed draft and callback failure without loss. Run tests and commit.
3. Loopback server and UI. Create server.py, cli.py, ui/index.html/app.js/styles.css, tests/test_server.py. open_review serves validated bundle and store with hooks; package assets in the built wheel. Provide demo and serve commands. Host/Origin/token/size/CSP/path allowlists; untrusted text safely rendered. Browser shows report/evidence and explicit support/coverage/defect fields. Click number -> source/table/operand; selecting passage fills locator/excerpt. No identity/machine labels in independent mode payload. Pause/reload/edit/submit with acknowledged revision, local saved and hook status separately. Test traversal/symlink/injection/CSRF, failed report and absence of hidden labels. Run unit and actual browser checks, save artificial-data evidence, commit.
4. Reusable hook lifecycle. Create hooks.py/tests/test_hooks.py. on_submission/on_revision receives immutable records and returns pending/succeeded/failed plus durable identifier. Idempotency and retries cannot duplicate external work; unknown external outcome stays pending until caller reconciliation. No built-in Git, provider client or model dispatch. Library/CLI consume same code. Test fake caller that fails, resumes and refuses stale result. Export full structured support judgments, no string-notes-only data. Commit.
5. Package/reproducibility. Add pyproject.toml, lockfile, build checks and packaging test. Wheel includes local UI and schemas. Synthetic example bundle boots without account or network, full install/run README and import example. Validate independent consumers with two synthetic bundle shapes; no test relies on application-specific report schema. Run complete suite and browser demo, produce versioned release candidate after package and license verification; do not upload to a registry automatically. Consumer installation pins exact Git commit or tested wheel SHA-256, never mutable main.
6. Repository contract and delivery. Add CONTRIBUTING.md, SECURITY.md, CODEOWNERS, issue/PR templates, measured test/coverage information and verify the existing MIT LICENSE is included in distributions. Public checks run only on relevant push/PR, least permissions, bounded time and no schedules; measure coverage over full source, establish measured floor rather than invent a badge. First implementation uses reviewed PR, not an unreviewed direct feature push. Report exact tests/browser evidence/known limitations. No self-merge without explicit authority.

## Required browser rehearsal
Use artificial bundle; select support and source spans, add material defect, reload after answer, amend submitted response and restore after simulated persistence/hook failure. Assert no manual metadata/JSON/path work, every numeric occurrence accounted for, blind payloads free of labels/identity, source hashes intact, old revisions retained and honest pending status. Compare manual actions and active time with existing CLI using counterbalanced synthetic packets; correctness must not degrade.

## Handoff
Read SPEC.md and this plan, implement task by task in an isolated worktree, use synthetic fixtures, run unit/contract/real-browser/package checks, then open a ready PR. This package is built and verified before an application consumer is integrated. The package contains only generic data contracts and review behavior.


## Capability extension plan — source-grounded human review (2026-10-08)

Baseline: v0.5.8, commit `500de606f847c399eaf09da993cb207290abc329`. This section records missing reusable capabilities after a source-level audit; it is not an implementation or browser-qualification claim. Historical bootstrap tasks above remain unchanged. Synthetic fixtures only; no consumer-specific sampling, grading policy, model orchestration or real reports belong in this package.

### Existing capabilities to reuse

- `ReviewBundle.fields`/`ReportField` display complete answers and context; `claims`, numeric spans and source bindings provide navigation.
- `FormField` supports caller-defined choice/text/boolean checklists, multiple judgments, evidence and explanation requirements. The caller supplies the actual checklist and materiality policy.
- `Selection`, source search/rendering and calculation cards provide frozen passages, input evidence, formula/result diagnostics and explicit unresolved states.
- `Defect`/`renderDefects` support reviewer-added material omissions with source passages even without an existing claim ID.
- `FileStore`, revisions, hooks and export retain drafts/submissions, elapsed review time, restart and caller backup status.
- Independent-task disclosure rejection and caller-provided blind markers exist. Callers control cross-task completion, authorization to disclose later comparisons, their own comparison artifacts and queue size. The package must not invent these policies.

### Missing reusable capability M1 — reviewer-selected answer annotations

**Observed gap:** `Selection` binds source lines, not answer passages. `store.validate_answers` rejects judgment keys absent from `bundle.form`; `Defect` links existing claims or free-text notes but lacks an exact answer-range binding. There is no durable generic supported/defective/unverifiable annotation for an arbitrary passage outside the caller's claim inventory. Free-text notes are insufficient to bind the reviewed occurrence reproducibly.

**Deliver:** allow the reviewer to select exact text in an original answer field and create a stable annotation carrying field path, exact offsets/text, report/document hash, disposition, reason, materiality where applicable and source selections. Support supported/defective/cannot-verify and explicit incomplete findings; do not force a potential issue to be declared defective. Keep omission records possible without an answer range. Use one versioned annotation contract shared by source selection, persistence, UI and export; retain original reports and old bundle/submission identities.

**Anchors:** `src/evidence_review/contracts.py` (`Selection`, `Judgment`, `Defect`, `ReviewSubmission`), `store.py::validate_answers`, `ui/app.js::fieldControl/renderDefects`, schemas and export. Extend existing primitives rather than a parallel review store.

**Acceptance:** real-browser selection of prose omitted from claims; repeated identical text bound to the chosen occurrence; Unicode offsets validated across browser/Python; supported and unverifiable notes persist; invalid/stale/out-of-bounds ranges fail; original bytes remain unchanged; draft/restart, stale tabs, amendments, export and restore preserve annotations. Old records still validate and hash identically. No automatic claim of exhaustive review.

### Missing reusable capability M2 — reviewer-authored calculation worksheet

**Observed gap:** calculation cards recompute caller-supplied formulas through `evidence.calculate`; the UI/server do not expose an editable, durable reviewer calculation. A wrong or missing supplied formula cannot be independently recomputed and saved as structured reviewer evidence within the app.

**Deliver:** an optional worksheet for reviewer-entered formula, operands, units/periods/entity/metric, operand source selections, reported value and tolerance. Reuse the bounded Decimal evaluator and existing source validation; label every worksheet as reviewer-authored and separate from candidate/prepared calculations. Link it to an existing subject or an M1 answer annotation. Save inputs, result or explicit calculation error, rationale and provenance through the existing store/export. Never overwrite the original formula or infer semantic support from arithmetic.

**Anchors:** `contracts.py::Calculation/Operand`, `evidence.py::calculate`, `server.py`, `ui/app.js` calculation rendering, `store.py`, schemas and export. M1 is needed only for links to newly selected answer passages.

**Acceptance:** create a worksheet when no formula was supplied; correct a deliberately wrong formula without altering it; attach multiple operand passages; retain unavailable operands and unknown support; reject unsafe/nonfinite expressions and retain division-by-zero errors explicitly. Browser recomputation, autosave/restart, revision and export must preserve the exact reviewer calculation. Existing formulas and historical submissions remain unchanged.

### Completion and ownership

Package maintainer owns M1/M2; tracking: [evidence-review-I40](https://github.com/nousergon/evidence-review/issues/40). Implement under a separately authorized implementation arc, run contract/unit/real-browser and full package checks, and publish a pinned release through the established release process. A consumer must separately qualify its complete workflow against that pin. Existing functionality above needs consumer fixtures, not duplicate package implementations.

SOTA: provenance-bound human annotations and reproducible safe calculations over immutable evidence. Delta: two generic extensions; sampling, judge comparison, review scope and scientific acceptance remain consumer responsibilities.

### Status: implemented in 0.6.0

M1 and M2 are implemented in 0.6.0 (tracking [evidence-review-I40](https://github.com/nousergon/evidence-review/issues/40)); the plan text above is kept as written. M1 is `answer-annotation/v1` (`AnswerRange`, `AnswerAnnotation` with `ann:` identities, `ReviewSubmission.annotations`, `Defect.answer_ranges`), with mouse selection and a keyboard path (answer field, exact text, occurrence) that yields the same code-point range. M2 is `reviewer-calculation-worksheet/v1` (`CalculationWorksheet`, `SubjectRef`, `ReviewSubmission.worksheets`) recomputed by the bounded Decimal evaluator. A worksheet links to an M1 annotation through `SubjectRef{kind: annotation, id: <annotation_id>}`; the id must exist in the same answers on every save and submission, so removing a linked annotation is refused, and each annotation card can start a worksheet about itself. Both contracts are in `capabilities()`. Verified locally on synthetic fixtures and in Chromium; the qualification record is [docs/qualification/0.6.0.json](docs/qualification/0.6.0.json). Human usability, screen-reader use and consumer qualification against the pin remain open.
