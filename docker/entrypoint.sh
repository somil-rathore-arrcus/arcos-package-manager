#!/bin/sh
# Container entrypoint: check that the runtime locations are usable, say what
# is configured - never a secret - and exec the command so it is the process
# that receives SIGTERM from `docker compose down`.
set -eu

uid="$(id -u)"
fail=0

writable() {  # writable <dir> <what>
    if [ ! -d "$1" ] || [ ! -w "$1" ]; then
        echo "apm: $2 $1 is not writable by uid $uid. Set APM_UID/APM_GID in .env" \
             "to the owner of the host directory (id -u / id -g) and rebuild, or" \
             "fix the directory's ownership." >&2
        fail=1
    fi
}

writable /app/out "output directory"
writable "${APM_WORKSPACE_DIR:-/var/tmp/arcos-package-manager}" "workspace"
writable "${APM_CACHE_DIR:-/var/cache/arcos-package-manager}" "cache"

key="${APM_GIT_SSH_IDENTITY_FILE:-}"
if [ -n "$key" ] && [ ! -r "$key" ]; then
    echo "apm: the SSH key mounted at $key is not readable by uid $uid. Set" \
         "APM_UID/APM_GID in .env to the key's owner and rebuild." >&2
    fail=1
fi
[ "$fail" -eq 0 ] || exit 1

if [ -n "$key" ]; then ssh_key="mounted read-only"; else ssh_key="none"; fi
if [ -n "${APM_GITHUB_TOKEN:-}" ]; then token="set"; else token="not set (read-only)"; fi
if [ -f /app/out/upstream-resolutions.json ]; then
    mapping="/app/out/upstream-resolutions.json"
elif [ -f /app/out/upstream-mapping.csv ]; then
    mapping="/app/out/upstream-mapping.csv"
else
    mapping="none yet - run: docker compose exec app apm resolve-release"
fi

echo "apm: ARCoS Package Manager starting as uid $uid on 0.0.0.0:8080"
echo "apm: git backend=${APM_GIT_BACKEND:-auto}, git ssh key=$ssh_key, github token=$token"
echo "apm: workspace=${APM_WORKSPACE_DIR:-/var/tmp/arcos-package-manager}, output=/app/out, packages file=${APM_PACKAGES_FILE:-/app/config/packages.yaml}"
echo "apm: mapping=$mapping"

exec "$@"
