#!/usr/bin/env bash
# Full local gate: lint, strict type-check of core/, and the test suite.
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
mypy --strict core/
pytest -q
