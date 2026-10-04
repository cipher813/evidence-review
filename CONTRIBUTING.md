# Contributing

Read SPEC.md and IMPLEMENTATION_PLAN.md. Use artificial fixtures only, describe observed defects and verification, and submit changes through pull requests. Do not add hosted services, telemetry or real customer/research material. Run the locked Python suite and actual Chromium tests before submitting; new behavior needs relevant tests and reproducible installation.

Run scripts/check.sh for the measured coverage gate (85% floor against an 87% baseline). Install Chromium first with uv run playwright install chromium. Checks run locally; no hosted workflow or scheduled job is enabled. PR authors supply verification evidence, and the owner reviews it.
