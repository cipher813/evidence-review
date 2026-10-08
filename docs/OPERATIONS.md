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

Pass `source_links={source_id: {"url": ..., "line_url": ..., "label": ..., "note": ...}}` to let reviewers check each frozen source against its public original. `url` opens the whole document; `line_url` is an optional template whose `{start}` and `{end}` are replaced with the cited line numbers (for example a Git host's `?plain=1#L{start}-L{end}`). Use `line_url` only when the original has the frozen copy's exact bytes, so its line numbers match; otherwise the link falls back to `url` plus a text fragment of the excerpt. Both must be https. Links are served beside the bundle and never change its hash. A source with no link says so in the evidence pane instead of implying an original was checked.

To bind evidence to an individual number, set `citations` (the located line or lines that hold the value) or `calculation` (formula plus operands, each with its own citation) on that `NumericSpan`. Span evidence is validated exactly like claim evidence. A number bound to a single located citation of a linked source renders as a link that opens that line of the original in a new tab and shows the same line in the evidence pane; a calculated number shows its formula and every input with its own link. Identifier spans cannot carry evidence.

The judgments pane is guided: fields with a `subject_id` are items, worked one at a time. The current item shows its statement or reference text, `help` and the meaning of each option (`option_help`), its decision and note, and "Next unanswered item" (keys: `n` next unanswered, `p` previous, ignored while typing); opening a statement from the report or the subject list makes its decision current. Claim checkboxes appear only on items whose subject is a reference item (coverage), labelled with claim text. Fields without a subject are under Finish. Mark task material such as the research question with `ReportField(role="context")`: it is shown apart from the answer, and its spans must be `identifier` with no claims or evidence. `ReviewBundle.instructions` is shown at the top as "What to do".

## Atomic source check and rendered sources (0.5.0)

`evidence_review.capabilities()` returns the package version, the contracts it understands and its presentation modes. Pass `require_contracts=(...)` to `open_review` to refuse an older package before serving, and `presentation_mode="atomic-source-check/v1"` to show statements above, one row per atom on the left and the rendered source on the right. An unknown mode or contract raises `evidence_review.server.UnsupportedContract` instead of being ignored.

- `atom_evidence` is an `atom-evidence/v1` manifest (or a callback taking the active bundle), built with `build_atom_manifest(bundle, facts)`. Quantities come from the bundle's own numeric inventory. Non-numeric facts are declared by the caller; the package never splits prose or decides support. In atomic mode, a missing manifest is built from the numeric inventory alone.
- `rendered_sources` is a list of `RenderedSourceAsset` (or a callback). Build one with `prepare_render_asset(bundle, source_id, OriginalAsset(bytes, media_type), atoms)`. Markdown whose bytes equal the frozen text renders faithfully; HTML and PDF render as sanitized derivatives with their lineage stated; anything else falls back to a readable rendering of the frozen text. Each target is `exact`, `page_only`, `ambiguous` (every candidate shown, none chosen) or `unavailable`, and the viewer says which.
- Atom checks are form fields: `numeric_span_id` for a quantity, `atom` (an `AtomBinding`) for a declared fact. A choice field's `numeric_verification_values` refuses those values until every check on the same subject is ticked. Opening a source never ticks a check.
- Sidecars never change bundle or submission identity. A bundle without them behaves exactly as in 0.4.x.

## Late hook results (0.5.0)

A continuation result that arrives after its attempt was fenced is recorded, never applied. The task log gains one of three events, each with the error class only (no message, payload or token):

- `hook_late_result_fenced`: a late result arrived and was ignored because a newer revision or attempt owns the task.
- `hook_late_settlement_failed`: recording the late result itself failed; the event says `reconciliation_required`.
- `hook_late_callback_error`: the late callback raised.

If even the event cannot be written, the failure is kept in memory (`evidence_review.hooks.unpersisted_late_failures()`) and logged at error level, so it is never silent. Treat any of these as a prompt to run your `reconcile` callback and check your own receipt.

## Evidence binding and navigation fencing (0.5.1)

0.5.1 closes four audit findings (evidence-review-I26 to I29). Bundles, submissions and saved judgments are unchanged; a manifest that 0.5.0 accepted can now be refused, and that refusal is the fix.

- **Atom targets are the atom's own evidence.** `validate_atom_evidence` refuses a quantity row whose `citation_target_ids` or `candidate_target_ids` name anything other than its own span's `citations` or that span's `prepared_evidence` citations, and a quantity `calculation_ref` other than `span:<its own span id>`. A fact row may target its linked claims' or references' citations, or a declared `AtomCitation` whose new `atom_ids` names that fact; its `calculation_ref` may be `claim:<a linked claim>` or `span:<a number inside the fact's own words>`. `build_atom_manifest` fills `atom_ids` itself. Each row target in `atom_view` carries a `binding` label (`span_citation`, `prepared_evidence`, `linked_statement_citation`, `declared_atom_citation`, and `calculation_input` on calculation leaves), shown in the row's details.
- **Exact means the context corresponds.** An HTML or PDF target is `exact` only when the hit's own text matches the cited frozen line: a table cell needs its row's cells, and the table's header row when the frozen table has one, to equal the frozen row; any other block must contain the whole cited line with its leading Markdown markup removed. This applies to a single hit too. A same-number hit under another metric or period becomes `unavailable` with a reason that says so. Ambiguous and unavailable targets keep their 0.5.0 meaning.
- **Literal hashes render.** `#Heading`, seven hashes, an indented hash or a lone `#` is literal paragraph text; only `#`–`######` followed by whitespace is a heading. Every Markdown parser branch consumes a line or records it as unsupported syntax.
- **Late responses never paint.** Each "Open source" click takes a navigation generation and captures the bundle and task. A rendered-source response or failure that arrives after a newer click or after "Next task" is dropped without painting or moving focus, and a pending request for another source is aborted where the browser allows.
- **One control per atom.** In atomic mode the statement context in the judgment item has no quantity checkbox; the atom row holds the only one.
- **Task noun.** `ReviewWorkload.task_noun` (for example `"Check"`) replaces the default "Answer"/"Task" label in the header, so a host shows "Check 1 of 3". It is optional display metadata: empty or absent keeps the old label and leaves bundle hashes unchanged.
