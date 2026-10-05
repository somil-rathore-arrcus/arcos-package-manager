# Sourced by the host runtime scripts (server.sh, apm, setup-venv.sh,
# build-frontend.sh): defaults for running on a host without Docker.
#
# .env is NOT sourced as shell - it holds unquoted values with spaces, and the
# application reads it itself. Only the variables these scripts need are read,
# and an exported environment variable always wins over .env.

APM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$APM_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

apm_env_value() {
    [ -f "$APM_ROOT/.env" ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" "$APM_ROOT/.env" | tail -1 | tr -d '\r'
}

: "${APM_HOST:=$(apm_env_value APM_HOST)}"
: "${APM_HOST:=0.0.0.0}"
: "${APM_PORT:=$(apm_env_value APM_PORT)}"
: "${APM_PORT:=8080}"
export APM_HOST APM_PORT

# Runtime state lives in out/, never in config/: a discovery run must not
# rewrite the committed config/packages.yaml and dirty the checkout.
if [ -z "${APM_PACKAGES_FILE:-}" ] && [ -z "$(apm_env_value APM_PACKAGES_FILE)" ]; then
    export APM_PACKAGES_FILE="$APM_ROOT/out/packages.yaml"
fi

APM_VENV="${APM_VENV:-$APM_ROOT/.venv}"
APM_RUN_DIR="${APM_RUN_DIR:-$APM_ROOT/out/run}"
