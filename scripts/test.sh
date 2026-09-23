#!/usr/bin/env bash
# Everything: backend tests, frontend typecheck, frontend tests, and the
# invariant checks against the generated mapping.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== backend =="
./.venv/bin/python -m pytest tests -q

echo "== frontend typecheck =="
(cd frontend && npx tsc --noEmit)

echo "== frontend tests =="
(cd frontend && npx vitest run)

if [ -f out/upstream-mapping.csv ]; then
  echo "== mapping invariants =="
  ./.venv/bin/python scripts/verify_output.py
fi

# The generated debian/upstream.md files must still say what the mapping says.
if [ -d out/upstream-md ] && [ -f out/upstream-mapping-bookworm.csv ]; then
  echo "== upstream.md matches the mapping =="
  ./.venv/bin/python scripts/verify_upstream_md.py bookworm
fi
