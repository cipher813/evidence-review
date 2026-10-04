#!/bin/sh
set -eu
uv run --frozen pytest --cov=evidence_review --cov-report=term --cov-fail-under=85
