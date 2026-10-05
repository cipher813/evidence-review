# Hardening register

Owner-approved scope: organizational transfer, enforced CI/security, whole-source coverage, measured badges and verified GitHub releases. Public MIT remains unchanged; no private material, hosted app, model calls or PyPI upload.

Baseline: transfer verified; 24 local tests, no required hosted quality check, stale README measurements, no tags/releases. Existing scanners and org PR/deletion/force-push rules are inherited. Missing quality gate, reproducible package delivery, badge evidence and fleet enrollment are registered in evidence-review-I4.

Graph: one quality job on every PR/main push/manual dispatch (no path-filtered missing checks); Python 3.12 with locked dependencies; actual Chromium, full unit/security suite, reproducible wheel/isolated install, denominator guard and locked dependency audit. Public standard runner, caches, no schedule/matrix, cancellation on superseded PRs only. Main-only publisher gets contents-write separately and consumes the exact tested immutable artifact. Release wheel/checksums/generated notes are idempotent; existing releases never replaced. No registry upload.

Coverage includes every Python package and delivery-script file, without omit lists. Initial floor cannot fall below prior whole-package 86.83% and ratchets after final measurement. Badges remain unknown until successful main CI publishes. Repo-specific required-check supplement is created only after an actual green context; organization protection remains the parent policy. Consumer keeps its tested code/content pin, with source URL updated deliberately and reverified. Fleet registration is a separate reviewed change.

Independent review: fixed fractional-floor rounding with precision=2; staged resumable drafts before publication; bound new version tags to source SHA; hash-locked build/runtime installation; refused stale-main badge updates. Regression tests cover each class. Existing published assets are never replaced. Enrollment/cost-scope register and both public roster mirrors are tracked as alpha-engine-config-I11991.

Ruling: public MIT, with standard hosted Actions and no premium runner/schedule/cloud resource. This follows the organization baseline and required-check approach; no extra release signing without a verifying consumer.

## 0.2.0 qualification (cold-cache distribution)

Baseline: main 5725b7e, CI run 37253109722 failed at `verify_distribution.py:111`. The clean install used `uv pip install --offline` against an empty runner cache, so it could only pass when a warm cache already held every runtime wheel. `uv lock --check --offline` in `check.sh` had the same hidden dependency.

Fix: `verify_distribution.py` downloads each runtime wheel named in `uv.lock` for the running interpreter, accepts it only if its SHA-256 matches both the lock and the exported requirement hashes, refuses missing, extra or altered wheels, and then installs with `--offline --no-index --find-links <wheelhouse> --require-hashes` and a fresh empty cache. The lock check runs online. The wheelhouse manifest, coverage and dependency audit are attached to each release so provenance outlives the 14-day CI artifact. Regression tests cover hash mismatch, missing compatible wheel, unlocked requirement, lock/requirement disagreement, incomplete or extra wheelhouse, altered staged bytes and non-identical second build.

Publisher reconciliation with evidence-review-I4: I4 proposed publishing on an explicit tag or release dispatch. The merged design publishes from the main-push run instead, which gives the same guarantees: it runs only after the quality job passes on that exact commit, consumes the tested artifact rather than rebuilding, binds the version tag to the source commit, refuses an existing version whose bytes differ (a different wheel needs a version bump), resumes an interrupted draft only for the same commit, and never uploads to a package registry. Required-check enforcement on main is repository configuration and is verified separately from this code.
