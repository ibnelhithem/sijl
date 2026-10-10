#!/usr/bin/env bash
# The exact checks CI runs (.github/workflows/ci.yml). Run before every commit.
# Extra arguments go to pytest, e.g. `scripts/check.sh -k identity`.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest "$@"
