#!/bin/bash
# Dalal Desk: double-click me in Finder. Terminal opens, sets everything up on the first run
# (a few minutes), then your browser opens the terminal at http://127.0.0.1:8000.
# Stop it with Ctrl+C or by closing this Terminal window.
cd "$(dirname "$0")" || exit 1
bash ./scripts/start.sh "$@" --open
status=$?
echo
read -r -p "Dalal Desk stopped (exit $status). Press Enter to close this window… " _
