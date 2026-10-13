#!/usr/bin/env bash
# Dalal Desk: run me from a terminal ( ./RUN-linux.sh ). Sets everything up on the first run
# (a few minutes), then your browser opens the terminal at http://127.0.0.1:8000.
# Stop it with Ctrl+C.
cd "$(dirname "$0")" || exit 1
exec bash ./scripts/start.sh "$@" --open
