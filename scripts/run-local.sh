#!/bin/bash
# Run the watcher locally. Needs TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID to send alerts.
# Extra env: ONLY_TARGET="substring"  DRY_RUN=1 (print only, save nothing)
BASE="${WATCHER_HOME:-$HOME/.flight-watcher}"
cd "$(dirname "$0")/.." || exit 1
exec "$BASE/xvfb-run.sh" "$BASE/venv/bin/python" watch.py "$@"
