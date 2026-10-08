# Source navigation and usable judgments qualification

Acceptance baseline: nous-ergon-ops-PR1594, plan 2026-10-06-evidence-review-usability; product evidence-review-I17; private integration Primer-I14.

## Whole gap register

| Task | Scope | State | Evidence |
|---|---|---|---|
| 1 | Source links, local fallback, served-asset/navigation diagnostics | Verified in package | Browser/API tests; diagnostics expose served asset hashes and exact bundle coverage |
| 2 | Private semantic preparation and source provenance | Private companion | Primer-I14 |
| 3 | Direct operand links, inline arithmetic, unresolved inputs | Verified in package | Contract/browser tests; recursive inputs and mathematical constants |
| 4 | Conservative readable tables, numbered fallback | Verified in package | Table cases include escaped pipes, blank boundary cells, duplicate labels and multirow headers |
| 5 | Report plus one active judgment; unchanged decisions/durability | Verified in package | Browser/store/hook tests; same-subject fields remain reachable; pending notes/passages not completed |
| 5a | Assigned workload, answer vs judgment counts | Package display verified; private companion owns filtering | Package display/private assignment tests |
| 6 | Package/adapter qualification, release, real re-export and human pilot | 0.5.0 qualified locally (docs/qualification/0.5.0.json) and released; 0.5.1 audit fixes qualified locally (docs/qualification/0.5.1.json); 0.5.2 recheck fixes qualified locally (docs/qualification/0.5.2.json) and released; 0.5.3 I12167 fixes qualified locally (docs/qualification/0.5.3.json) and released; 0.5.4 plan-conformance fixes qualified locally (docs/qualification/0.5.4.json) and released; 0.5.5 operand context and author declarations qualified locally (docs/qualification/0.5.5.json) and released; 0.5.6 I12170 line-target proof and table header grid qualified locally (docs/qualification/0.5.6.json) and released; 0.5.7 I12170 P3 explicit header scope qualified locally (docs/qualification/0.5.7.json) and released; 0.5.8 real-corpus fixes (blank-line ranges, under-located ambiguous numbers) qualified locally (docs/qualification/0.5.8.json); no human pilot commissioned (optional) | Human acceptance remains separate |
| 7 | Atomic rows: one stable row per number or declared fact, unresolved rows retained (2026-10-07 plan WP8) | Verified in package; 0.5.8: an ambiguous number with fewer than two located candidates is one unavailable row with a reason instead of refusing the bundle | tests/test_atomic_evidence.py, tests/test_ambiguous_atom_degrade.py |
| 8 | One-click rendered target with exact, page-only or ambiguous highlight; offline sanitized rendering (WP9) | Verified on synthetic HTML, PDF and Markdown fixtures; 0.5.4: long-table column header, row label, units and footnote stay in view, caller exact targets must be proven by the derivative; 0.5.6: exact line targets proven against the frozen source, table header context from the occupied-cell grid with "unavailable" where ambiguous; 0.5.7: explicitly named headers classified by declared scope before geometry; 0.5.8: citation ranges spanning blank lines exact over their non-blank lines | tests/test_source_rendering.py, tests/test_browser_rendered_sources.py, tests/test_long_table_context.py, tests/test_render_target_proof.py, tests/test_render_line_correspondence.py, tests/test_table_header_grid.py, tests/test_blank_line_ranges.py |
| 9 | Three-pane source-check layout; clicks never check; keyboard, zoom and denied storage (WP10, WP4) | Verified in Chromium; 0.5.4: distinct accessible names for row expansion, checkbox and link, HTML header scope where table structure proves it; screen reader and human pilot not performed | tests/test_atomic_source_workflow.py, tests/test_layout_recovery.py, tests/test_long_table_context.py, tests/test_html_fidelity.py |
| 10 | Late hook settlement failures are durable and sanitized (WP5) | Verified in package | tests/test_hooks.py |

Baseline: origin/main 0d071b5 (0.3.0), 97 tests passed locally on macOS/Python 3.12.13. New table/context tests: 16 expected feature failures, 2 existing validation cases passed; then all 18 passed. Navigation/browser regressions: 4 failed on baseline, to be rechecked after implementation. Safe URL regressions: 4 failed on baseline.

## Binding constraints

Immutable source provenance; candidate evidence retained; independent prepared evidence separately attributed; navigation is exposure only; all required fields, rubric, blinding, sample and aggregation retained. No source upload, model spending, hosting or human verdict supplied by software. Synthetic public fixtures only.

## Rulings

- Execute the existing accepted implementation plan under the current development mandate; no new design approval needed. If the mandate were narrower, private adapter changes would need to be removed.
- Incomplete source evidence withholds the displayed recomputed result; raw deterministic arithmetic remains available as a diagnostic under recomputation.arithmetic. This implements task3 and supersedes baseline tests that displayed an arithmetic success despite unresolved inputs.
- Extend v1 with optional provenance/workload fields; old inputs remain accepted. Regenerate schema copies and use new export identities rather than mutating existing submissions. Old wheels cannot parse new optional fields, so consumer pin upgrade must follow package release.

SOTA: immutable provenance, independent evidence-linked judgments, conservative table parsing, task-based usability qualification. Delta: GitHub highlights rows; local preview highlights a cell only with a unique excerpt in an established rectangular table. Automated browser results do not establish human usability.

## Review and qualification evidence

Fresh frontier review found same-subject routing, blank boundary table cells, per-number ambiguity fallback and query locator compatibility defects. Each has a regression observed failing before repair. Required explanation/passage completion and ambiguous source choices were also tested red then green.

Package quality command: `bash scripts/check.sh` (full unit/browser/security/consumer-contract suite, whole-source coverage, both schema copies, reproducible wheels, empty-cache hash-verified installation). Final results are recorded below and in PR/CI artifacts. Release publication remains the existing main-only workflow after the human merge.

## Remaining gates

- Package release/integration: merge package PR first; existing main-only release mechanics publish the verified wheel. The private companion pins exact commit/wheel/content together. No production deployment or manual publication instruction is added.
- Human usability: under the canonical 2026-10-07 plan a zero-assistance human pilot is optional and only runs if separately commissioned; it is not a rehearsal gate. Any human evaluation work is the consuming study's decision and is recorded there, not in this package. Human usability and reviewer effort therefore stay explicitly unverified; automation cannot measure whether a person needs assistance. (Earlier guidance made the pilot mandatory; superseded 2026-10-08.)
- Real missing evidence is represented as unresolved, never filled with inferred financial values. It can be judged under the existing rubric, but broken destinations/evidence controls cannot qualify.

Live workload is a caller-validated display sidecar, refreshed for the active bundle without rewriting its saved identity. Source-link callbacks revalidate the next task's eligible frozen sources before advancing. Initial bundle workload remains its immutable audit snapshot.

Final local qualification (macOS, Python 3.12.13): 132 tests passed; whole-source Python coverage 93.93% against 92% floor; table parser 100%; both schema copies equal model-generated schemas; identical wheels from independent builds; hash-verified empty-cache installation passed. Locked runtime/build dependency audit found no known vulnerabilities. Source secret scan found no leaks. Hosted CI is assessed separately on the PR. No human usability result is claimed.

Final review: fresh frontier reviewer; same-subject navigation, blank boundary table alignment, unresolved per-number fallback and provider-neutral query locator findings all reproduced red and repaired green. No deferred minor finding.
