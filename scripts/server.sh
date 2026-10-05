#!/usr/bin/env bash
# Run the ARCoS Package Manager on this host, without Docker.
#
# One uvicorn process serves the dashboard at / and the API at /api on
# APM_HOST:APM_PORT (default 0.0.0.0:8080), so the browser sees one origin and
# no web server or second port is needed. It keeps running after logout.
#
#   scripts/server.sh start | stop | restart | status | logs
#
# Needs scripts/setup-venv.sh (once) and scripts/build-frontend.sh (after
# frontend changes). Logs: out/run/server.log.
set -euo pipefail
. "$(dirname "$0")/env.sh"

PIDFILE="$APM_RUN_DIR/server.pid"
LOG="$APM_RUN_DIR/server.log"
case "$APM_HOST" in 0.0.0.0|::|"") CHECK_HOST=127.0.0.1 ;; *) CHECK_HOST="$APM_HOST" ;; esac
HEALTH="http://$CHECK_HOST:$APM_PORT/api/health"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

healthy() {
    "$APM_VENV/bin/python" - "$HEALTH" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

start() {
    if running; then
        echo "already running (pid $(cat "$PIDFILE")) on $APM_HOST:$APM_PORT"
        return 0
    fi
    [ -x "$APM_VENV/bin/uvicorn" ] || { echo "no venv: run scripts/setup-venv.sh first" >&2; exit 1; }
    if [ ! -f "$APM_ROOT/frontend/dist/index.html" ] && [ -z "${APM_FRONTEND_DIST:-}" ]; then
        echo "warning: no frontend build; run scripts/build-frontend.sh. Serving the API only." >&2
    fi
    mkdir -p "$APM_RUN_DIR"
    cd "$APM_ROOT"
    echo "--- start $(date -u +%Y-%m-%dT%H:%M:%SZ) on $APM_HOST:$APM_PORT" >>"$LOG"
    nohup setsid "$APM_VENV/bin/uvicorn" apm.api.main:app \
        --host "$APM_HOST" --port "$APM_PORT" >>"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    for _ in $(seq 1 45); do
        if healthy; then
            echo "started (pid $(cat "$PIDFILE")): http://$APM_HOST:$APM_PORT/"
            return 0
        fi
        if ! running; then
            echo "the server exited; last log lines:" >&2
            tail -n 20 "$LOG" >&2
            rm -f "$PIDFILE"
            exit 1
        fi
        sleep 1
    done
    echo "started, but $HEALTH did not answer within 45s; see $LOG" >&2
    exit 1
}

stop() {
    if ! running; then
        echo "not running"
        rm -f "$PIDFILE"
        return 0
    fi
    pid="$(cat "$PIDFILE")"
    kill "$pid"
    for _ in $(seq 1 15); do
        kill -0 "$pid" 2>/dev/null || { rm -f "$PIDFILE"; echo "stopped"; return 0; }
        sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped (forced)"
}

status() {
    if running && healthy; then
        echo "running (pid $(cat "$PIDFILE")) on $APM_HOST:$APM_PORT - $HEALTH answers"
    elif running; then
        echo "running (pid $(cat "$PIDFILE")) but $HEALTH does not answer; see $LOG"
        return 1
    else
        echo "not running"
        return 1
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    logs) tail -n "${2:-100}" "$LOG" ;;
    *) echo "usage: $0 start|stop|restart|status|logs [lines]" >&2; exit 2 ;;
esac
