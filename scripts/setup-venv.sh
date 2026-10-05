#!/usr/bin/env bash
# Create the project's own Python virtualenv (.venv) and install into it.
# Nothing is installed system-wide and no root is needed - not even Debian's
# python3-venv package: without ensurepip the venv is created without pip and
# pip is bootstrapped into it from bootstrap.pypa.io.
#
#   scripts/setup-venv.sh          runtime dependencies
#   scripts/setup-venv.sh --dev    plus the test dependencies (pytest)
set -euo pipefail
. "$(dirname "$0")/env.sh"

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
    echo "Python 3.9 or newer is required ($PY is $("$PY" --version 2>&1))" >&2
    exit 1
}

if [ ! -x "$APM_VENV/bin/python" ]; then
    if "$PY" -c 'import ensurepip' 2>/dev/null; then
        "$PY" -m venv "$APM_VENV"
    else
        "$PY" -m venv --without-pip "$APM_VENV"
    fi
fi

if ! "$APM_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "bootstrapping pip into $APM_VENV"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    "$PY" -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        https://bootstrap.pypa.io/get-pip.py "$tmp/get-pip.py"
    "$APM_VENV/bin/python" "$tmp/get-pip.py" --quiet --no-warn-script-location
fi

requirements="$APM_ROOT/requirements.txt"
[ "${1:-}" = "--dev" ] && requirements="$APM_ROOT/requirements-dev.txt"
"$APM_VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$requirements"

# `apm` inside the venv: the CLI from this checkout, with the host defaults.
cat > "$APM_VENV/bin/apm" <<WRAPPER
#!/bin/sh
exec "$APM_ROOT/scripts/apm" "\$@"
WRAPPER
chmod 755 "$APM_VENV/bin/apm"

echo "venv ready: $APM_VENV ($("$APM_VENV/bin/python" --version))"
