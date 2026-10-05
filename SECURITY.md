# Security

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/nousergon/evidence-review/security/advisories/new). Maintainer @cipher813 targets acknowledgement within three business days and provides a remediation status within seven days. These are response targets, not a service SLA.

Do not post private documents, credentials, session URLs or exploit details in public issues. The app binds to loopback, processes untrusted local documents and keeps review state on your computer. Keep its session URL and state directory private. The current supported version is the latest GitHub release; reports affecting the unreleased main branch are also welcome.

## Threat model

The app is a single-user tool on one computer. It protects review state and blinding from other local web pages and from untrusted document content; it does not defend against another process running as the same operating-system user, which can read the state directory directly.

| Threat | Control |
|---|---|
| Another website calling the local API (CSRF, DNS rebinding) | Listener on 127.0.0.1 only; exact Host check; exact Origin check on writes; per-session bearer token kept out of history and server logs |
| Script or markup in reports and sources | Rendered as text nodes only; restrictive Content-Security-Policy with no inline script, remote assets or framing |
| Path traversal or symlink escape | Fixed asset allowlist; task ids validated; symlinked state roots, task directories, logs and snapshots refused |
| Oversized or malformed requests | 2 MB body limit, JSON only, unknown fields rejected, schema validation before any write |
| Lost or overwritten answers | Write-ahead log with fsync before acknowledgement, optimistic revisions, idempotency keys, immutable submitted revisions |
| Identity or machine labels shown to an independent reviewer | Independent bundles cannot carry disclosures; optional `blind_markers` refuse bundles and withhold responses containing caller-declared markers |
| Unknown external side effects | Hook outcomes are recorded as pending, failed or unknown and never replayed automatically |

Out of scope: hosted or multi-user deployment, authentication beyond the session URL, encryption at rest (use disk encryption), and model calls (the package makes none).
