# Evidence Review design

Status: approved direction; implementation not yet built. Local-first reusable Python package and browser app.

## Purpose
An outside user can inspect a report against provided evidence and record human judgments without copying identifiers, locating source files or editing JSON. The application automates evidence assembly, navigation, metadata, autosave, revisions and export. It does not determine factual truth on behalf of the human.

## Public boundary
Only generic schemas, viewer/persistence code, documentation and clearly synthetic examples belong here. No real research corpus, customer documents, experiment prompts, rubrics, credentials, model configuration, evaluation labels or live results. No application-specific module names or directory assumptions. No hosted deployment or package-registry publication is implied by repository creation.

## Interfaces
Versioned ReviewBundle contains bundle_id, document/content hashes, task kind, report fields with exact text, source documents, quantitative spans, claims, formulas/operands, neutral reference items and caller-supplied form schema. Numeric spans retain field path and exact Unicode offsets. Each has explicit cited/derived/uncited/ambiguous/unavailable/identifier state; every number in prose is accounted for. Sources have full frozen text, hash, metadata, locators and context; derived numbers expose every operand and its source. Arithmetic verification is not support verification.

ReviewSubmission contains bundle_id/hash, revision, assessor, UTC times, active/session time, explicit judgments, selected claim/source-span IDs, defects with category/materiality/evidence note, completion and provenance. Preserve every previous revision. The consumer defines assessment aggregation and its rubric, not the package.

Package API: validate_bundle(data)->ReviewBundle; open_review(bundle, store, hooks)->ReviewHandle; export_submission(store, task_id, revision)->ReviewSubmission. CLI evidence-review demo runs entirely on artificial data; evidence-review serve --bundle PATH --state-dir PATH opens the loopback browser. HTTP state and callbacks are shared between CLI and library.

Storage protocol has load_task, append_event, save_snapshot and save_submission with revision/idempotency checks. A filesystem implementation uses atomic writes, fsync and write-ahead replay; callers may supply another store. Hooks on_submission/on_revision receive immutable submissions and return workflow status: pending/succeeded/failed with recording identifier. Remote backup/provider/orchestrator behavior is caller-owned.

## Human interface
Report left, evidence right, judgment controls nearby. Click a number/claim to see exact cited lines, table headers/context/footnotes and full source. Calculations show formula, each input/unit/period/source, recomputation and discrepancy. Missing source/input is unresolved, never hidden. Support/coverage choices and verified defect/materiality remain explicit human input; identifiers/locators/timing/derived flags are automatic. No default clean/support verdict.

Independent review hides machine labels and producing-system identities; no semantic proposed verdicts appear in browser payloads. Adjudication is a separate caller-authorized task only after independent submission. Sources/report markup are untrusted, rendered safely. Full report completion requires explicit acknowledgement, not just evidence pane clicks.

## Reliability and privacy
127.0.0.1 only, session token, Origin/Host/CSRF validation, restrictive CSP and path/size allowlists. No remote assets, telemetry, arbitrary shell commands, script execution from report or source, or symlink/path escape. Multiple tabs use optimistic revisions and idempotency keys. Drafts are not submitted. No acknowledged answer loss; changed bundle/rubric hash rejects stale edits. Display local save and hook/remote status separately.

## Acceptance
Synthetic unsupported facts, omitted prose numbers, wrong unit/period, missing operand/source, duplicate quantities, bps/ranges/negative amounts, table footnotes and failed reports remain inspectable. Browser tests prove evidence navigation, save/reload/amend/submit without JSON/path copying; blinding, injection, concurrency, crash and callback failures have regression coverage. No claim of accuracy/coverage/performance without measured evidence. The project uses MIT. Package/license inclusion and release verification apply before a distributable release.
