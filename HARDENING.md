# Hardening register

Owner-approved scope: organizational transfer, enforced CI/security, whole-source coverage, measured badges and verified GitHub releases. Public MIT remains unchanged; no private material, hosted app, model calls or PyPI upload.

Baseline: transfer verified; 24 local tests, no required hosted quality check, stale README measurements, no tags/releases. Existing scanners and org PR/deletion/force-push rules are inherited. Missing quality gate, reproducible package delivery, badge evidence and fleet enrollment are registered in evidence-review-I4.

Graph: one quality job on every PR/main push/manual dispatch (no path-filtered missing checks); Python 3.12 with locked dependencies; actual Chromium, full unit/security suite, reproducible wheel/isolated install, denominator guard and locked dependency audit. Public standard runner, caches, no schedule/matrix, cancellation on superseded PRs only. Main-only publisher gets contents-write separately and consumes the exact tested immutable artifact. Release wheel/checksums/generated notes are idempotent; existing releases never replaced. No registry upload.

Coverage includes every Python package and delivery-script file, without omit lists. Initial floor cannot fall below prior whole-package 86.83% and ratchets after final measurement. Badges remain unknown until successful main CI publishes. Repo-specific required-check supplement is created only after an actual green context; organization protection remains the parent policy. Consumer keeps its tested code/content pin, with source URL updated deliberately and reverified. Fleet registration is a separate reviewed change.

Independent review: fixed fractional-floor rounding with precision=2; staged resumable drafts before publication; bound new version tags to source SHA; hash-locked build/runtime installation; refused stale-main badge updates. Regression tests cover each class. Existing published assets are never replaced. Enrollment/cost-scope register and both public roster mirrors are tracked as alpha-engine-config-I11991.

Ruling: public MIT, with standard hosted Actions and no premium runner/schedule/cloud resource. This follows the organization baseline and required-check approach; no extra release signing without a verifying consumer.
