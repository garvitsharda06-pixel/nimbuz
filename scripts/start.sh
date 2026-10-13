#!/usr/bin/env bash
# Dalal Desk: one-command local start (macOS / Linux / WSL).
#
#   ./scripts/start.sh                # http://127.0.0.1:8000
#   ./scripts/start.sh 8080           # pick another port
#   ./scripts/start.sh 8000 --demo    # synthetic data only: bypass the gate to watch demo cards flow
#   PORT=8080 ./scripts/start.sh      # same as passing the port
#
# First run installs everything (Python 3.12 venv, UI build), then generates the SYNTHETIC
# demo dataset and runs the walk-forward research once. Later runs start in seconds.
# The server binds 127.0.0.1 only (your machine); see README "Phone access" for Tailscale.
set -euo pipefail

PORT="${PORT:-8000}"
case "${1:-}" in
  '' | -*) ;;                        # no port given: keep the default, the rest are serve flags
  *) PORT="$1"; shift ;;
esac
cd "$(dirname "$0")/.."

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

case "$PORT" in
  '' | *[!0-9]*) die "port must be a number, got '$PORT' (example: ./scripts/start.sh 8080)" ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "port must be 1-65535, got $PORT"

# 1) backend (Python 3.12 virtualenv)
if [ ! -x backend/.venv/bin/dalal ]; then
  say "Installing the backend (first run only)…"
  if command -v uv >/dev/null 2>&1; then
    (cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .)
  elif command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv backend/.venv
    backend/.venv/bin/pip install --upgrade pip
    backend/.venv/bin/pip install -e ./backend
  else
    die "need Python 3.12 or uv. Easiest: install uv (it fetches Python 3.12 for you):
       curl -LsSf https://astral.sh/uv/install.sh | sh     then re-run this script."
  fi
fi
DALAL=backend/.venv/bin/dalal

# 2) frontend (built once into frontend/dist and served by the backend)
if [ ! -f frontend/dist/index.html ]; then
  command -v npm >/dev/null 2>&1 || die "need Node.js 18+ to build the UI once: https://nodejs.org"
  say "Building the terminal UI (first run only)…"
  (cd frontend && npm ci --no-audit --no-fund && npx vite build)
fi

# 3) data + research (synthetic demo unless you switched config/settings.yaml to provider: upstox)
PROVIDER=$(awk '/^data:/{d=1} d && /^[[:space:]]+provider:/{print $2; exit}' config/settings.yaml)
PROVIDER=${PROVIDER:-synthetic}
if [ "$PROVIDER" = "synthetic" ] && [ ! -f data/synthetic/instruments.json ]; then
  say "Generating the SYNTHETIC demo dataset (plumbing only, never market data)…"
  "$DALAL" synth
fi
if [ "$PROVIDER" = "upstox" ] && [ ! -f data/upstox/instruments.json ]; then
  die "provider is upstox but no data yet: put UPSTOX_ANALYTICS_TOKEN in .env, then run
       $DALAL verify && $DALAL download-data"
fi
if [ ! -f "data/$PROVIDER/research/statsbook.json" ]; then
  say "Running the walk-forward research once (a few minutes)…"
  "$DALAL" backtest
fi

# 4) serve (fail early with a clear message if the port is taken)
if ! backend/.venv/bin/python - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # same as uvicorn
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
PY
then
  die "port $PORT is already in use. Pick another one, e.g.  ./scripts/start.sh 8080"
fi
say "Dalal Desk is starting → open  http://127.0.0.1:${PORT}   (Ctrl+C to stop)"
[ "$PROVIDER" = "synthetic" ] && say "Data is SYNTHETIC: pick a day in the Replay strip and press ▶ Start."
exec "$DALAL" serve --port "$PORT" "$@"
