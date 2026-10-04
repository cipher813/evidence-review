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

Verification: 20 tests passed on Python 3.12/macOS; whole-package Python coverage 91%. Clean wheel import/schema/UI/license checks passed. No workflow or model calls. Browser timings are automated tests, not measured human effort.
