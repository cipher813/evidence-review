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
| Original HTML that runs script, loads remote or private resources, or phishes through links (0.5.0 rendered sources) | Rendered offline into a closed-vocabulary node tree the page builds with text nodes; script, style, iframe, object, form, svg and similar elements dropped; event-handler and URL attributes dropped; links made inert; images replaced by a notice; no innerHTML, iframe or CSP change. Test: zero outbound requests from a hostile fixture |
| Malformed or hostile PDF (decompression bombs, deep nesting, huge pages) | In-house text-layer parser with byte, page, node and work limits (`RenderLimits`); a limit breach is a refused rendering, not a partial one; scanned pages become page-only targets |
| Rendered derivative swapped or altered after preparation | Each derivative is hash-bound to its manifest and the bundle; the server re-verifies it on every `/api/source-render` and `/api/source-target` response and refuses a mismatch |
| A row navigating to evidence that is not its own (0.5.1) | `validate_atom_evidence` refuses targets and calculation references outside the atom's own span, attributed prepared evidence, linked statement or attributed declaration; HTML and PDF `exact` requires frozen-to-original context correspondence, so the same number under another metric or period is never shown as the cited location |
| A sidecar hiding a check control the frozen form requires, or claiming one it does not bind (0.5.2) | `validate_atom_evidence` requires each row's `form_field_id` to be exactly the control the immutable bundle binds to that quantity or fact, refusing omitted, wrong and unknown ids before anything is served |
| Launch token left in the URL, or startup hanging, when the browser denies tab storage (0.5.3) | The fragment is read and scrubbed from the address bar before any storage call; storage is optional and guarded, the token falls back to page memory and is never written to localStorage; with no token the page sends no request and shows a recovery message |
| A caller-supplied render manifest claiming an exact location its derivative does not hold (0.5.4) | `validate_render_asset`, run by `open_review` before anything is served, requires every named node and page to exist, every ambiguous candidate to hold its excerpt, and every `exact` target to equal the package's own deterministic mapping of that derivative; anything else is refused like a hash mismatch. The viewer never announces an exact highlight it cannot show |
| Local styling in original HTML used to hide, overlay or fetch (0.5.4) | Only closed-grammar presentational declarations survive (alignment, weight, style, decoration, vertical alignment, white-space, indents up to three digits); colour, `url()`, position, size and everything else are removed and counted; the browser applies them through CSSOM, so the page CSP still forbids inline styles and the derivative check refuses any other declaration |
| An author's own claim of support or tolerance passing for evidence (0.5.5) | `Claim.support_declaration` and `Calculation.declared_tolerance` are display text only: shown labelled as the author's, never parsed, never a citation or target, never used in recomputation, evidence state, a check control or the verified-verdict gate; they pass the same `blind_markers` check as the rest of the bundle |
| A late or failed rendered-source response painting over a newer click or task (0.5.1) | Navigation generation plus captured bundle and task identity fence every success and error path; superseded requests are aborted where possible and fenced regardless |
| Rendered source leaking blinded identity | Derivatives and manifests pass the same `blind_markers` check as bundles outside adjudication |

Out of scope: hosted or multi-user deployment, authentication beyond the session URL, encryption at rest (use disk encryption), and model calls (the package makes none).
