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

## Row/control binding and prepared evidence in atomic rows (0.5.2)

0.5.2 closes two audit recheck findings (evidence-review-I31, I32). Bundles, submissions and saved judgments are unchanged; a manifest that 0.5.1 accepted can now be refused, and that refusal is the fix.

- **A row names exactly its frozen check.** When the immutable bundle's form has a check bound to a quantity (`numeric_span_id`) or a fact (`atom.atom_id`), that atom's row must carry the same `form_field_id`. An omitted (null or absent) id, a different control's id, or an id the form does not have is refused by `validate_atom_evidence`, and therefore by `open_review` before anything is served, with an error naming the atom and the expected control. An atom with no bound check in the form stays legitimately unassigned (`form_field_id` null) and may not claim a control. `build_atom_manifest` already fills the id, so callers that use it need no change.
- **Prepared evidence is shown, never substituted.** `AtomEvidenceItem.prepared_refs` lists `prepared:<span id>:<index>` references into a span's `prepared_evidence`. `build_atom_manifest` fills it for every quantity; a quantity must list exactly its own span's records, in order, so known preparation cannot be hidden, and a fact may list only records of numbers written inside its own words. A reference to another occurrence, an unknown index, or a `calculation_ref` naming a preparation is refused. The field is omitted from the serialized manifest when empty, so manifests without preparation are byte-identical to 0.5.1.
- **Rows show preparation separately.** Each `atom_view` row gains `preparations`: reference, label ("Independently prepared calculation"), origin, provenance, the preparation's reason, direct-citation targets (`prepared_evidence`) and calculation leaves (`prepared_calculation_input`), nested operands included. The original `evidence_state`, `targets`, `calculation` and `calculation_leaves` are unchanged and stay the original candidate evidence. The row expansion labels the two apart; opening prepared evidence never checks the row or sets a verdict.

## Denied tab storage and partial prepared inputs (0.5.3)

0.5.3 closes audit finding alpha-engine-config-I12167. Bundles, submissions, saved judgments and atom identities are unchanged; a manifest that 0.5.2 accepted can be refused when it hides a known prepared input, and that refusal is the fix.

- **Tab storage is optional.** The page reads the launch token from the URL fragment and removes the fragment before it touches storage. The token and the assessor name go through a guarded tab store with an in-memory fallback, so a browser that denies `sessionStorage` (the accessor, `getItem` or `setItem`) still loads, navigates, saves and submits. The token is never written to `localStorage`.
- **Safe recovery.** If the page has no token (for example a reload when storage is denied), it sends no request and says to reopen the review from the launch link printed by the review command; saved judgments live on the server, so nothing is lost. A token the server rejects shows the same recovery instead of a raw error.
- **Partial prepared inputs are shown, never substituted.** `AtomEvidenceItem.prepared_input_refs` lists `prepared-input:<span id>:<index>` references into a span's `prepared_inputs`. `build_atom_manifest` fills it for every quantity; a quantity must list exactly its own span's inputs, in order, and a fact may list only inputs of numbers written inside its own words. Other occurrences, unknown or non-canonical indices and duplicates are refused. The field is omitted when empty, so manifests without prepared inputs serialize as before.
- **Rows show them beside the original inputs.** Each `atom_view` row gains `prepared_inputs`: reference, name, value, unit, period, entity, provenance, the original input's own status and reason, the located source (binding `prepared_input`) and, for a derived input, its calculation and every part's source. The original `calculation_leaves`, including unresolved ones, are unchanged. Opening one never checks the row.

## Table context, render-target proof and row names (0.5.4)

0.5.4 closes the package findings of the 2026-10-08 plan-conformance sweep. Bundles, submissions, saved judgments, atom identities and the `source-render-manifest/v1` schema are unchanged. A rendered asset that 0.5.3 accepted can be refused when one of its exact targets is not proven, and that refusal is the fix.

- **Long tables keep their context.** In `atomic-source-check/v1` the rendered document scrolls inside the viewer, so the source title, target status and context line stay visible, and table header cells are sticky. After one click the context line quotes, from the rendering itself, the cited cell's column header (period, unit), row label, the caption or the line above the table (often the units), the section heading, `tfoot` and the footnotes that follow the table. It is presentation only: nothing is inferred and nothing is saved. Below 1000 px the document scrolls within 70% of the window height.
- **Caller render manifests must be proven.** `validate_render_asset(bundle, asset)` (also run by `open_review` for every supplied `RenderedSourceAsset`) checks the hash binding and node allowlist as before, then refuses an asset when a target names a node or PDF page the derivative does not contain, an ambiguous candidate does not hold the excerpt, the derivative belongs to another source or render mode, or an `exact` target is not exactly what the package's deterministic mapping derives from the derivative for that citation (same nodes, and for PDF the same page and box). The same number in another row, period or metric is therefore never accepted as exact. A caller may claim less than is proven (for example `unavailable`). Assets built by `prepare_render_asset` or `render_frozen_text` pass this check by construction.
- **Viewer defence in depth.** If a served exact target's node is missing, the status reads "Location unavailable" and nothing is highlighted; the viewer never says "Exact location highlighted" without a highlight.
- **Row names.** Each atom row's expansion control is named "Calculation, inputs and sources for “…” (atom N)" or "Evidence details for “…” (atom N)", distinct from the row's checkbox and source link. Visible text is unchanged.
- **HTML fidelity.** Header cells without `scope` get `col` in `thead` or an all-header first row and `row` when they start a body row; any other header cell is left alone. Allowlisted local styling is kept as declared above; both are listed in the manifest's transforms, so derivative hashes of HTML originals with such markup differ from 0.5.3's.

## Operand context and author declarations (0.5.5)

0.5.5 adds three optional, display-only fields. Submissions, saved judgments, atom identities, the atom and render sidecar schemas, recomputation and every check control are unchanged. Each defaults to `""`; an empty value is left out of serialization, so a bundle sealed before 0.5.5 keeps its `bundle_hash`, while a supplied value is hashed like every other field.

- **`Operand.metric`** names what an input measures (for example "operating margin"), next to `entity`, `period` and `unit`. `atom_view` prepared-input rows carry `metric`, and calculation operands carry it when supplied. Expanded calculation rows show it after the unit (`22.8 pct · operating margin · FY2025`, repeated only where it differs from the enclosing input), the value's tooltip names entity and metric, and prepared inputs list value and unit, metric, period and entity.
- **Tolerance in view.** Every expanded calculation shows the numeric tolerance in the result's unit (`Tolerance ±0.01 bps`) without opening its checks. `tolerance` stays a non-negative Decimal absolute tolerance.
- **`Calculation.declared_tolerance`** is the author's prose tolerance (for example "0.1 percentage point"). It is shown beside the numeric tolerance as the author's declared tolerance and in the calculation checks; it is never parsed and never reaches recomputation.
- **`Claim.support_declaration`** is the author's declared support status and reason (for example "inference: derived from the margin trend"). `atom_view` rows gain `author_declarations` (claim id, label, declaration, provenance) for the row's own claims; the browser shows it as "Author's declaration" in the expanded row, the calculation card and the evidence pane. It is never a citation or target, never checks a row and never changes an evidence state, a reason or the verified-verdict gate.

## Line-target proof and table header grid (0.5.6)

0.5.6 fixes alpha-engine-config-I12170. Bundles, submissions, saved judgments, atom identities and the atom and render sidecar schemas are unchanged.

- **Line-faithful derivatives are re-derived, not trusted.** A `normalized_snapshot` or `faithful_markdown` derivative is a deterministic function of the frozen text, so `validate_render_asset` (and `open_review`) rebuilds it with the package's own renderer and requires the supplied tree to equal it node for node: same ids, text, `line` labels and table cells. Exact targets are checked first, so the error names the target whose lines changed; then the whole tree, because source-render serves every node as highlight context. A rehashed derivative is refused even with no exact target. Derivatives prepared by this package's `prepare_render_asset` or `render_frozen_text` are unaffected; any other line-mode derivative, including one an earlier version accepted, is refused. HTML (`faithful_html`) and PDF target proofs are unchanged.
- **Per-request revalidation.** `/api/source-render/<id>` and `/api/source-target/<id>` re-run the full shared proof before answering, not only the hash and node allowlist, and answer 500 if it fails.
- **Table header context from the grid.** The context line reads a cell's column and row headers from an occupied-cell grid of its own table: rowspan and colspan count across rows and end at their row group (thead, each tbody, tfoot), explicit `headers`/`id` win, then `scope`, then grid position. A hit in a nested table never reads the outer table. A missing, unresolved or ambiguous association is shown as `Column: unavailable` / `Row: unavailable`, in the visible context and its accessible description; no period is guessed. A first row counts as a header row only when every cell is `th`, and a row label comes only from a header cell, so a first column of `td` cells no longer gives a row label.
- **HTML cleaning.** Table cells keep `id` and `headers` as `data-cell-id` and `data-headers` when every token matches `[A-Za-z0-9_.:-]{1,64}`, declared in the manifest's transforms; header scope is inferred from grid position, so a header cell behind a rowspan is judged by where it sits. Derivative hashes of HTML originals with such markup differ from 0.5.5's.

## Explicit header scope (0.5.7)

0.5.7 fixes the residual P3 of alpha-engine-config-I12170. Bundles, submissions, saved judgments, atom identities, the atom and render sidecar schemas, derivatives and render manifests are unchanged; only the browser's context line changes.

- **Declared scope classifies an explicitly named header.** When a cell names its headers through `headers`/`id`, each named header's valid `scope` decides first: `row` or `rowgroup` heads the cell's row, `col` or `colgroup` its column. So a `scope="rowgroup"` header above the cell in the same `tbody` reads as a row group (`Column: FY2025 · Row: Retail / Operating margin`), not as a column header. Only a named header with no valid scope (for example a `td`, or a `th` the cleaner could not prove a scope for) is classified by geometry: overlapping the cell's rows heads its row, otherwise its column. Same-table resolution and the refusal of duplicated or unresolved ids are unchanged. The server's `_infer_header_scope` adds a scope only where none is given, so it never overrides a declared one.

## Blank-line ranges and under-located ambiguous numbers (0.5.8)

0.5.8 fixes two defects found by Primer's real-corpus sweep. Bundles, submissions, saved judgments, atom identities, the atom and render sidecar schemas, derivatives and the render proof are unchanged; which targets are exact and which atom rows are unavailable changes.

- **Citation ranges across blank lines.** In `normalized_snapshot` and `faithful_markdown` a blank (whitespace-only) frozen line has no rendered node. A range that spans one is now exact over the nodes of its non-blank lines, in order; blank lines at either edge are trimmed, so a range ending on a blank line after a table row still narrows to that row's one matching cell. A range of blank lines only is `unavailable` ("Cited lines are blank in the frozen text"), and the excerpt must occur in the range's whitespace-normalised text ("Cited excerpt does not occur in the cited frozen lines" otherwise), the same check `evidence.validate_citation` makes. The sweep found 565 such citations over 62 ranges, covering 67 of 130 registry quantities, all previously `unavailable`. A node inserted for a blank line is still refused by the canonical-tree proof.
- **Ambiguous numbers without two located candidates.** The bundle contract accepts a `NumericSpan` in state `ambiguous` with zero or one located citation. `build_atom_manifest` used to pass that straight to an atom that requires two candidates and refuse the whole bundle; the sweep measured 87 of 213 bundles (341 spans) refused that way. Such a span is now one `unavailable` row whose reason begins "Ambiguous: several candidate sources and fewer than two located on this number" and keeps the producer's reason; every other row builds as before. A single located candidate is never shown as the number's location, and `validate_atom_evidence` refuses a caller manifest that shows an ambiguous number as `located`. A span with two or more distinct located candidates stays `ambiguous` with every candidate.
