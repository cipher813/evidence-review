# Implementation register

Plan: IMPLEMENTATION_PLAN.md. Baseline: documentation only, main 9a798cd.

| Requirement | State | Verification |
|---|---|---|
| Versioned contracts; complete numeric inventory; source/locator integrity; Decimal math | verified locally | tests/test_contracts.py, observed missing implementation |
| Transactional durable storage, revisions, replay, concurrency, idempotency | verified locally | tests/test_store.py |
| Loopback browser UI, evidence/form navigation, autosave, amendments, blinding/security | verified locally | tests/test_server.py, tests/test_browser.py, tests/test_security.py |
| Immutable submission hooks, durable outcomes, caller-owned continuation | verified locally | tests/test_hooks.py |
| Locked packaging, schemas/assets/license, two consumers, clean install | verified locally | tests/test_packaging.py; clean noneditable wheel install, UI/schema/license inspected |
| Offline browser rehearsal, action/timing evidence, public documentation | verified synthetic workflow; human timing not claimed | tests/test_browser.py, tests/test_browser_failures.py |

Ruling: generic form fields carry caller-required decisions; no inference of semantic truth.
Ruling: no hosted service or registry upload; exact tested commit/wheel is the consumer pin.

Verification: 24 tests passed on Python 3.12/macOS; whole-package Python coverage 87%. Clean wheel import/schema/UI/license checks passed. No workflow or model calls. Browser timings are automated tests, not measured human effort.

Final wheel built twice with identical SHA-256: 4bb2735471cbb1bd605703df1ac33b6960ce0737a406e7dc863a9b5cc5201987. Clean noneditable wheel install and CLI help verified.

Fresh whole-branch review findings closed with regression tests: on-input text saving; explicit negative boolean decisions versus truth-required acknowledgement; count/date classification; metadata and tolerance display. All public fixtures remain artificial.

Ruling: local coverage gate and PR/issue templates complete repository delivery. Hosted CI is deferred to an external-contributor need, preserving the owner's minimal Actions preference; local evidence is not described as CI green.
