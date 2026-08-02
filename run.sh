#!/bin/sh
# PRISM - start the API (port 8000) and the web client (port 5173) together.
#
# Creates backend/.venv if it is missing, installs backend dependencies if the
# marker file backend/.venv/.prism-deps-installed is missing, runs npm install if
# frontend/node_modules is missing, then starts both servers. Ctrl+C stops both.
#
# Delete backend/.venv/.prism-deps-installed to force a dependency reinstall.

set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
VENV_PY="$VENV/bin/python"
MARKER="$VENV/.prism-deps-installed"
REQUIREMENTS="$BACKEND/requirements.txt"

API_PORT=8000
WEB_PORT=5173

API_PID=""
WEB_PID=""

fail() {
    echo "run.sh: $1" >&2
    exit 1
}

port_in_use() {
    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi
    if command -v nc >/dev/null 2>&1; then
        if nc -z 127.0.0.1 "$1" >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi
    # No way to check on this machine. Let the server report the conflict.
    return 1
}

stop_one() {
    pid="$1"
    if [ -z "$pid" ]; then
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    # npm and uvicorn --reload both fork, so signal the children first.
    if command -v pkill >/dev/null 2>&1; then
        pkill -TERM -P "$pid" 2>/dev/null || true
    fi
    kill -TERM "$pid" 2>/dev/null || true
}

force_one() {
    pid="$1"
    if [ -z "$pid" ]; then
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    if command -v pkill >/dev/null 2>&1; then
        pkill -KILL -P "$pid" 2>/dev/null || true
    fi
    kill -KILL "$pid" 2>/dev/null || true
}

cleanup() {
    status="${1:-0}"
    # Disarm first so a second signal, or the EXIT trap, cannot re-enter this.
    trap - INT TERM EXIT
    echo ""
    echo "Stopping PRISM."
    stop_one "$WEB_PID"
    stop_one "$API_PID"
    sleep 1
    force_one "$WEB_PID"
    force_one "$API_PID"
    wait 2>/dev/null || true
    exit "$status"
}

# --- Preflight. Check everything before creating or installing anything. -----

PY=""
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
fi
if [ -z "$PY" ]; then
    fail "Python was not found on PATH. Install Python 3.10 or newer, then run this script again."
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    fail "Python 3.10 or newer is required. '$PY' is older than that. Install a newer Python and run this script again."
fi

if ! command -v node >/dev/null 2>&1; then
    fail "node was not found on PATH. Install Node 20 or newer, then run this script again."
fi
if ! command -v npm >/dev/null 2>&1; then
    fail "npm was not found on PATH. It ships with Node - reinstall Node, then run this script again."
fi

if [ ! -f "$REQUIREMENTS" ]; then
    fail "backend/requirements.txt is missing. The backend is not set up yet - restore that file and run this script again."
fi
if [ ! -f "$FRONTEND/package.json" ]; then
    fail "frontend/package.json is missing. The frontend is not set up yet - restore that file and run this script again."
fi

if port_in_use "$API_PORT"; then
    fail "Port $API_PORT is already in use. Stop the process using it, then run this script again."
fi
if port_in_use "$WEB_PORT"; then
    fail "Port $WEB_PORT is already in use. Stop the process using it, then run this script again."
fi

if [ ! -f "$BACKEND/.env" ]; then
    echo "Note: backend/.env is missing. PRISM will start, but generating a"
    echo "      proposal will return 503 until GEMINI_API_KEY is set there."
    echo ""
fi

# --- Backend environment -----------------------------------------------------

if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment in backend/.venv"
    "$PY" -m venv "$VENV"
fi
if [ ! -x "$VENV_PY" ]; then
    fail "The backend/.venv directory exists but has no working interpreter at $VENV_PY. Delete backend/.venv and run this script again."
fi

if [ ! -f "$MARKER" ]; then
    echo "Installing backend dependencies"
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r "$REQUIREMENTS"
    : > "$MARKER"
fi

# --- Frontend environment ----------------------------------------------------

if [ ! -d "$FRONTEND/node_modules" ]; then
    echo "Installing frontend dependencies"
    (cd "$FRONTEND" && npm install)
fi

# --- Start both servers ------------------------------------------------------

trap 'cleanup 0' INT TERM EXIT

echo "Starting API on port $API_PORT"
(cd "$BACKEND" && exec "$VENV_PY" -m uvicorn app.main:app --reload --port "$API_PORT") &
API_PID=$!

echo "Starting web client on port $WEB_PORT"
(cd "$FRONTEND" && exec npm run dev -- --port "$WEB_PORT") &
WEB_PID=$!

echo ""
echo "  PRISM"
echo "  Web client   http://localhost:$WEB_PORT"
echo "  API          http://localhost:$API_PORT"
echo "  API health   http://localhost:$API_PORT/api/health"
echo ""
echo "  Press Ctrl+C to stop both."
echo ""

# Wait until one of them exits, then take the other down with it.
while :; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "The API stopped. Shutting down the web client too."
        break
    fi
    if ! kill -0 "$WEB_PID" 2>/dev/null; then
        echo "The web client stopped. Shutting down the API too."
        break
    fi
    sleep 1
done

# Reaching here means a server stopped on its own, which is a failure.
cleanup 1
