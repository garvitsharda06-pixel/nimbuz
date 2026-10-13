#!/usr/bin/env bash
# Dalal Desk: one-command local start (macOS / Linux / WSL). Works with macOS's bash 3.2.
#
#   ./scripts/start.sh                # first free port from 8000 → http://127.0.0.1:8000
#   ./scripts/start.sh 8080           # exactly this port (stops if it is taken)
#   ./scripts/start.sh 8000 --demo    # synthetic data only: bypass the gate to watch demo cards flow
#                                     # (the port can go anywhere among the flags)
#   ./scripts/start.sh --open         # also open the browser when the server is up
#   PORT=8080 ./scripts/start.sh      # same as passing the port
#
# First run installs everything (Python 3.12 venv, UI build if not prebuilt), then generates the
# SYNTHETIC demo dataset and runs the walk-forward research once. Later runs start in seconds.
# The server binds 127.0.0.1 only (your machine); see README "Phone access" for Tailscale.
set -euo pipefail

FIXED_PORT=0
if [ -n "${PORT:-}" ]; then FIXED_PORT=1; fi
PORT="${PORT:-8000}"
EXTRA=()                             # everything that is not the port goes to `dalal serve`
prev=""
for a in "$@"; do
  case "$prev" in
    --port) PORT="$a"; FIXED_PORT=1; prev=""; continue ;;
    --day | --speed | --start-time) EXTRA+=("$a"); prev=""; continue ;;   # serve flags that take a value
  esac
  case "$a" in
    --port=*) PORT="${a#--port=}"; FIXED_PORT=1 ;;
    --port) ;;
    -*) EXTRA+=("$a") ;;
    *) PORT="$a"; FIXED_PORT=1 ;;
  esac
  prev="$a"
done
cd "$(dirname "$0")/.."

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNote:\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

case "$PORT" in
  '' | *[!0-9]*) die "port must be a number, got '$PORT' (example: ./scripts/start.sh 8080)" ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "port must be 1-65535, got $PORT"

# 0) uv (fetches Python 3.12 for us) unless Python 3.12 is already installed
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"     # where the uv installer puts it
if [ ! -x backend/.venv/bin/dalal ] && ! command -v uv >/dev/null 2>&1 && ! command -v python3.12 >/dev/null 2>&1; then
  echo "Dalal Desk needs Python 3.12. The easiest way is 'uv', a small installer from astral.sh"
  echo "that downloads Python 3.12 into your home folder (nothing system-wide)."
  ans=""
  if [ -t 0 ]; then read -r -p "Install uv now? [Y/n] " ans || ans="n"; else ans="n"; fi
  case "$ans" in
    '' | [Yy]*)
      if command -v curl >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh
      elif command -v wget >/dev/null 2>&1; then wget -qO- https://astral.sh/uv/install.sh | sh
      else die "need curl or wget to install uv"; fi
      command -v uv >/dev/null 2>&1 || die "uv was installed but is not on PATH yet: open a new terminal and run this again" ;;
    *) die "install uv (https://docs.astral.sh/uv/) or Python 3.12, then run this again" ;;
  esac
fi

# 1) backend (Python 3.12 virtualenv); reinstalls when backend/pyproject.toml changes
STAMP=backend/.venv/.dalal-installed
if [ ! -x backend/.venv/bin/dalal ] || [ ! -f "$STAMP" ] || [ backend/pyproject.toml -nt "$STAMP" ]; then
  say "Installing the backend (first run, or its dependencies changed; a few minutes)…"
  if command -v uv >/dev/null 2>&1; then
    (cd backend && { [ -x .venv/bin/python ] || uv venv --python 3.12 .venv; } \
      && uv pip install --python .venv/bin/python -e .)
  else
    [ -x backend/.venv/bin/python ] || python3.12 -m venv backend/.venv
    backend/.venv/bin/python -m pip install --upgrade pip
    backend/.venv/bin/python -m pip install -e ./backend
  fi
  touch "$STAMP"
fi
DALAL=backend/.venv/bin/dalal
PY=backend/.venv/bin/python

# 2) frontend: served from frontend/dist (prebuilt in the desktop package, else built once with Node 18+)
if [ ! -f frontend/dist/index.html ]; then
  command -v npm >/dev/null 2>&1 || die "the UI is not built and Node.js is missing. Install Node.js 18+ (https://nodejs.org) and run this again"
  say "Building the terminal UI (first run only)…"
  (cd frontend && npm ci --no-audit --no-fund && npx vite build)
elif [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print 2>/dev/null | head -n 1)" ]; then
  if command -v npm >/dev/null 2>&1; then
    say "UI sources changed: rebuilding the terminal UI…"
    (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npx vite build)
  else
    warn "UI sources are newer than frontend/dist but Node.js is missing, so the existing build is used."
  fi
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

# 4) serve: an explicit port must be free; the default moves to the next free one
port_free() {
  "$PY" - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # same as uvicorn
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
PY
}
if ! port_free "$PORT"; then
  [ "$FIXED_PORT" = 1 ] && die "port $PORT is already in use. Pick another one, e.g.  ./scripts/start.sh 8080"
  first=$PORT
  while ! port_free "$PORT"; do
    PORT=$((PORT + 1))
    [ "$PORT" -le $((first + 50)) ] || die "ports $first-$PORT are all in use. Pick one, e.g.  ./scripts/start.sh 9000"
  done
  warn "port $first is busy, using $PORT instead."
fi
say "Dalal Desk is starting → open  http://127.0.0.1:${PORT}   (Ctrl+C to stop)"
[ "$PROVIDER" = "synthetic" ] && say "Data is SYNTHETIC: pick a day in the Replay strip and press ▶ Start."
exec "$DALAL" serve --port "$PORT" ${EXTRA[@]+"${EXTRA[@]}"}     # bash 3.2-safe empty-array expansion
