#!/usr/bin/env bash
# Run the frontend dev server, proxying /api to a locally running backend.
set -euo pipefail
cd "$(dirname "$0")/../frontend"
[ -d node_modules ] || npm install
export VITE_API_TARGET="${VITE_API_TARGET:-http://127.0.0.1:8000}"
exec npx vite --port "${VITE_PORT:-5173}"
