# Implementation register

Published: 0.2.1 ([release](https://github.com/nousergon/evidence-review/releases/tag/v0.2.1), tag commit 5c34d3f51d67f71b8b0c47b23f6a1a9f8fee4fa1, wheel sha256 abe7f81239489661edbaace03acffa68dd1adc5e2fcf2b14648f5ab6e653fed3). 0.2.0 was never published: its release step failed on a draft lookup, fixed in 0.2.1 with no package change. 0.2.2: atomic continuation-attempt claims and fenced late results (issue #12). 0.2.3: citations link to the public original at the cited lines; unresolved numbers prefill the frozen-source search. Current release candidate: 0.3.0 (per-number evidence: a span may carry its own citations or calculation, validated like claim evidence; a number bound to one located line is a direct link to that line). Earlier bootstrap plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Hardening and release process: [HARDENING.md](HARDENING.md).

| Requirement | State | Verification |
|---|---|---|
| Versioned contracts (v1 unchanged in 0.2.0); complete numeric inventory including dates, ratios, multiples, accounting negatives and reference numerals; source/locator integrity; Decimal math | verified locally | tests/test_contracts.py, tests/test_evidence.py |
| Evidence display: cited lines with paragraph, table header and table notes; each operand's own evidence; arithmetic separated from input-evidence status, unit and period warnings | verified locally | tests/test_evidence.py, tests/test_browser_review.py |
| Transactional durable storage, revisions, replay, concurrency, idempotency, injected fsync/rename/write failures | verified locally | tests/test_store.py, tests/test_faults.py |
| Stale tab and changed-bundle recovery without silent overwrite | verified locally | tests/test_browser_review.py, tests/test_store.py |
| Loopback security, untrusted text, wrong/missing token, traversal variants, symlinks | verified locally | tests/test_server.py, tests/test_security.py |
| Blinding: no disclosures in independent tasks; caller blind markers refuse bundles and withhold responses; browser traffic, page and history checked | verified locally | tests/test_security.py, tests/test_browser_review.py |
| Keyboard-only flow, accessible names carrying number state, visible focus, non-colour markers, narrow viewport | verified locally in Chromium; assistive-technology check not yet performed by a person | tests/test_browser_review.py |
| Hooks: immutable submissions, pending/failed/unknown outcomes, timeout without replay, late results never overwrite newer revisions | verified locally | tests/test_hooks.py, tests/test_faults.py |
| Hook attempt ownership: compare-and-set claim under the task lock; competing callers and reconciliations on separate stores run the callback once; late or superseded results fenced by revision, attempt and claim; restart after a claim does not replay; pre-0.2.2 state still reconciles. Not exactly-once remote effects | verified locally | tests/test_hooks.py |
| Navigation counts recorded as exposure, never verification | verified locally | tests/test_faults.py |
| Locked packaging; clean install from hash-verified wheelhouse with empty cache; wheelhouse faults and second-build mismatch fail before publish | verified locally | tests/test_delivery.py; `UV_CACHE_DIR="$(mktemp -d)" bash scripts/check.sh` |
| Independent second consumer using only exported API, no research or provider imports | verified locally | tests/test_consumer_contract.py |

Ruling: generic form fields carry caller-required decisions; no inference of semantic truth.
Ruling: no hosted service or registry upload; exact tested commit/wheel is the consumer pin.

Local verification for 0.2.2 (Linux, Python 3.12, uv 0.9.5, empty package cache): full suite green; whole-source Python coverage above the 92.0 floor; two identical wheel builds; clean isolated install from the staged wheelhouse. Hosted CI results are recorded on the pull request and release, not here. Browser timings are automated tests, not measured human effort; no human usability study has been run.
