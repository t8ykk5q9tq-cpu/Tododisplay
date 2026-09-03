#!/bin/bash
# update.sh - Pull the latest code and restart the display only if it changed.
# Intended to be run on a schedule (see cron setup in the README).
#
# Respects the same env vars as start-lite.sh, e.g. ROTATE, ITEM_PT, etc.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG="$SCRIPT_DIR/update.log"

# Trim the log so it never grows without bound: keep only the last 200 lines.
if [ -f "$LOG" ]; then
    tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

echo "----- $(date) -----" >> "$LOG"

# Record the current commit so we can tell if anything changed.
BEFORE=$(git rev-parse HEAD 2>/dev/null)

# Discard any local file-mode/whitespace changes that would block the pull
# (e.g. the executable-bit differences we've hit before), then pull.
git reset --hard >> "$LOG" 2>&1
git pull >> "$LOG" 2>&1

AFTER=$(git rev-parse HEAD 2>/dev/null)

if [ "$BEFORE" != "$AFTER" ]; then
    echo "Code changed ($BEFORE -> $AFTER). Restarting display." >> "$LOG"
    bash stop.sh >> "$LOG" 2>&1
    sleep 2
    # Relaunch in the background, detached, so cron doesn't hang waiting on it.
    DISPLAY="${DISPLAY:-:0}" ROTATE="${ROTATE:-90}" \
        nohup bash start-lite.sh >> "$LOG" 2>&1 &
    echo "Restarted." >> "$LOG"
else
    echo "No changes; display left running." >> "$LOG"
fi
