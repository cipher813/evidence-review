# Contributing

Read [SPEC.md](SPEC.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Open an issue describing the observed defect or proposed behavior, then submit a pull request with synthetic fixtures and test evidence. Maintainer @cipher813 reviews changes before merge. Record AI assistance in the PR model-attribution field.

Use Python 3.12 and the locked development environment:

```sh
uv sync --frozen
uv run --frozen playwright install chromium
bash scripts/check.sh
```

The check runs all tests, actual Chromium, whole-source Python coverage, two reproducible wheel builds and a clean isolated installation. Coverage includes package code and Python delivery scripts; the floor ratchets from the measured baseline. Hosted CI also audits locked runtime dependencies. Change coverage configuration only with matching scope tests and evidence.

Do not add hosted services, telemetry or private research/customer material. Never commit credentials, local review state or agent instruction files. Keep generic schemas, source assets and root schemas consistent. Bump the semantic version in pyproject.toml whenever the release wheel changes; the main publisher refuses to replace an existing version with different content.
