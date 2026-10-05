# Operating Evidence Review

Guidance for teams embedding the package or running the local app. Contracts are in [SPEC.md](../SPEC.md); the threat model is in [SECURITY.md](../SECURITY.md).

## Supported installation

Python 3.12 on macOS or Linux. Install an exact reviewed version, never a moving branch:

- a GitHub release wheel, after checking it against that release's `SHA256SUMS` and `package-verification.json`; or
- an exact commit: `pip install "evidence-review @ git+https://github.com/nousergon/evidence-review.git@<full-commit-sha>"`.

Record the wheel SHA-256 (or commit) in your project's lock file. Runtime dependency: `pydantic` only, at the version pinned in `pyproject.toml`. Each release also publishes `wheelhouse.json`, the hash-verified runtime wheels its clean install used.

## Schema compatibility

`review-bundle/v1` and `review-submission/v1` are stable. Within v1, a release may add derived values inside existing open fields (for example new keys in a calculation's `recomputation`), new optional API endpoints and new provenance keys, but never removes or renames a field, changes a type or relaxes a validation. A breaking change ships as `v2` with a migration note and both versions validated side by side for at least one release.

Numeric extraction is part of validation: a bundle states every numeric occurrence, and validation recomputes the inventory. A release that improves extraction (0.2.0 added calendar dates, ratios, multiples, accounting negatives and reference numerals) can therefore reject a bundle produced against an older release. Treat a package upgrade as a requalification: regenerate bundles, rerun your producer and consumer tests, then update the pin. Never upgrade the package underneath a frozen study or an in-flight review queue.

## Upgrade and migration

1. Read the release notes and diff `schemas/` between the two versions.
2. Finish or export in-flight tasks on the old version (`evidence-review export`).
3. Regenerate bundles with your producer, validate them (`evidence-review inspect --bundle bundle.json`), and rerun your consumer tests.
4. Pin the new wheel hash. Existing state directories keep working: the event log format is append-only JSON lines and earlier revisions stay readable.

A task whose bundle hash changed is refused (`bundle or rubric changed`) rather than silently attached to old answers. Start a new task id for a changed bundle; keep the old state for provenance.

## Recovery

State lives in `<state-dir>/<task-id>/`:

- `events.jsonl` is the write-ahead log and the source of truth. Each acknowledged save is appended and fsynced before the browser shows "Saved locally".
- `snapshot.json` is a convenience copy and may be stale; it is never read for recovery.
- `torn-*.bin` holds a partial final line from an interrupted write. It was never acknowledged; the store truncates it from the log on the next read and keeps the bytes for inspection.

After a crash, restart with the same bundle and state directory. A save whose acknowledgement was lost is either absent or present exactly once: the browser looks up its idempotency key in the saved state and shows "Not saved" if the key is absent, and a repeated request with the same key returns the original result instead of writing again. A continuation (hook) interrupted mid-call stays `pending`; one that exceeded `Hooks.timeout` is `unknown`. Neither is retried automatically: resolve it with your `reconcile` callback, which must check your own receipt (for example a remote commit or object id) before reporting success. Every invocation and reconciliation first claims its attempt in the state directory under the task lock, so two callers or processes never both run it; a reconciliation started while another attempt is still within `Hooks.timeout` returns the pending record without calling you. A crashed attempt's claim expires after `Hooks.timeout`, after which reconciliation can proceed. Claims bound how often the package calls you, not how often a remote effect happens, so keep callbacks idempotent. State written by 0.2.1 and earlier, which has no claim fields, is read and reconciled as before.

## Data retention, export and deletion

The app keeps everything locally and sends nothing anywhere. The state directory holds the assessor name, answers, every submitted revision, navigation counts and hook receipts.

- Export a submitted revision as canonical JSON: `evidence-review export --state-dir DIR --task TASK --revision N`. Output is byte-identical for the same revision.
- Retention is the caller's decision. Back up the state directory with your own tooling through a hook; the package never pushes or uploads.
- To delete a task, stop the app and remove `<state-dir>/<task-id>/`. Removal cannot be undone and loses the revision history, so export first if you need provenance.

## Blinding

Pass `blind_markers=[...]` to `open_review` with identity and machine-label strings your project must not show during independent review (model names, system codes, judge verdict tokens; at least three characters each). Outside `adjudication` tasks, a bundle containing a marker is refused and any API response containing one is withheld. This guards against accidental leakage; it does not replace building blinded bundles in the first place.
