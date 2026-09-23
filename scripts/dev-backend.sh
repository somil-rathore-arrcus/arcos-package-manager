#!/usr/bin/env bash
# Run the API for local development.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$PWD/src"
exec ./.venv/bin/uvicorn apm.api.main:app --reload --host "${APM_HOST:-127.0.0.1}" --port "${APM_PORT:-8000}"
