#!/bin/sh
set -eu
uv lock --check --offline
uv run --frozen coverage erase
uv run --frozen coverage run -m pytest
uv run --frozen coverage run --append scripts/verify_distribution.py
uv run --frozen coverage json --fail-under=0 -o artifacts/coverage.json
uv run --frozen coverage run --append scripts/badges.py
uv run --frozen coverage json --fail-under=0 -o artifacts/coverage.json
uv run --frozen python -c 'import json; from pathlib import Path; from scripts.verify_distribution import verify_scope; verify_scope(Path.cwd(), json.loads(Path("artifacts/coverage.json").read_text()))'
uv run --frozen coverage report
uv run --frozen python scripts/badges.py
