#!/usr/bin/env bash
# Build Dalal-Desk.zip for a Desktop: the committed sources (git HEAD) + a prebuilt UI, so the
# owner needs neither git nor Node.js. No venv, data or journal goes in: the RUN files set
# those up on the owner's machine.
#
#   ./scripts/package.sh [OUT_DIR]      # default OUT_DIR: ./package
set -euo pipefail
cd "$(dirname "$0")/.."
OUT_DIR="${1:-package}"
mkdir -p "$OUT_DIR"
OUT_DIR=$(cd "$OUT_DIR" && pwd)
NAME=Dalal-Desk
[ -z "$(git status --porcelain --untracked-files=no)" ] || echo "Note: uncommitted changes are NOT packaged (git HEAD only)." >&2

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar --prefix="$NAME/" HEAD | tar -x -C "$STAGE"
# build the UI from exactly the packaged sources (dist ends up newer than src, so no rebuild later)
(cd "$STAGE/$NAME/frontend" && npm ci --no-audit --no-fund --loglevel=error && npx vite build --logLevel warn \
  && rm -rf node_modules)
{
  echo "Dalal Desk desktop package"
  echo "commit: $(git rev-parse HEAD)"
  echo "branch: $(git rev-parse --abbrev-ref HEAD)"
  echo "built:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$STAGE/$NAME/PACKAGE-INFO.txt"
rm -f "$OUT_DIR/$NAME.zip"
(cd "$STAGE" && zip -qr -X "$OUT_DIR/$NAME.zip" "$NAME")   # zip keeps the exec bit on RUN-mac/linux
echo "$OUT_DIR/$NAME.zip ($(du -h "$OUT_DIR/$NAME.zip" | cut -f1))"
