#!/bin/bash
# sync.sh - Push local code changes to the Raspberry Pi.
#
# Usage (run from your Mac, inside the project folder):
#   ./sync.sh              # sync once
#   ./sync.sh --watch      # sync continuously on every file change
#
# Configure the Pi target below (or set PI_HOST / PI_PATH env vars).

PI_HOST="${PI_HOST:-pi@raspberrypi.local}"
PI_PATH="${PI_PATH:-~/Tododisplay}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

do_sync() {
    rsync -az --delete \
        --exclude 'venv/' \
        --exclude 'lists.db' \
        --exclude '.git/' \
        --exclude '__pycache__/' \
        ./ "${PI_HOST}:${PI_PATH}/"
    echo "$(date '+%H:%M:%S') synced -> ${PI_HOST}:${PI_PATH}"
}

if [ "$1" = "--watch" ]; then
    echo "Watching for changes. Press Ctrl+C to stop."
    do_sync
    if command -v fswatch >/dev/null 2>&1; then
        # fswatch is efficient; install with: brew install fswatch
        fswatch -o -l 0.5 \
            --exclude 'venv' --exclude 'lists.db' --exclude '.git' --exclude '__pycache__' \
            . | while read -r _; do
            do_sync
        done
    else
        echo "Tip: 'brew install fswatch' for instant syncing. Falling back to polling."
        while true; do
            sleep 2
            do_sync
        done
    fi
else
    do_sync
fi
