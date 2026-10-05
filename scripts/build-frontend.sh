#!/usr/bin/env bash
# Build the dashboard into frontend/dist (typecheck included).
#
# Uses npm when the host has it. A host without Node - such as the deployment
# host - builds it in Docker's node image instead, and only the built files
# are copied out; nothing is installed on the host.
set -euo pipefail
. "$(dirname "$0")/env.sh"
cd "$APM_ROOT/frontend"

if command -v npm >/dev/null 2>&1; then
    npm ci
    npm run build
elif command -v docker >/dev/null 2>&1; then
    docker build -q --target build -t apm-frontend-build . >/dev/null
    container="$(docker create apm-frontend-build)"
    trap 'docker rm -f "$container" >/dev/null 2>&1' EXIT
    rm -rf dist.new
    docker cp "$container:/app/dist" dist.new
    rm -rf dist
    mv dist.new dist
else
    echo "neither npm nor docker is available to build the frontend" >&2
    exit 1
fi
echo "built $APM_ROOT/frontend/dist"
