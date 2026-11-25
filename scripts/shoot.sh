#!/usr/bin/env bash
# Usage: scripts/shoot.sh <url> <out.png> [width] [height]
# Full-resolution screenshot with headless Chrome (local only, not used in CI).
set -euo pipefail
CHROME="${CHROME:-/c/Program Files/Google/Chrome/Application/chrome.exe}"
PROFILE="$(mktemp -d)"
OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
timeout 90 "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="${SCALE:-1.5}" \
  --user-data-dir="$(cygpath -w "$PROFILE")" --window-size="${3:-1440},${4:-1000}" \
  --virtual-time-budget="${BUDGET:-8000}" --screenshot="$(cygpath -w "$OUT")" "$1" >/dev/null 2>&1
rm -rf "$PROFILE"
