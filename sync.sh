#!/bin/bash
# School Plan Daily Synchronizer & Notifier
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

CDP_URL="${SCHOOL_CDP_URL:-http://127.0.0.1:9222}"
CDP_URL="${CDP_URL%/}"

rc=0
if curl -s -m 2 "${CDP_URL}/json/version" >/dev/null 2>&1; then
  python3 "$DIR/ai_refresher.py" --no-load || rc=$?
else
  python3 "$DIR/generate_calendar.py" || rc=$?
fi

echo "[$(date '+%F %T')] sync rc=$rc" >> "$DIR/sync.log"
