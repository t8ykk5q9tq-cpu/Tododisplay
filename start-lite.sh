#!/bin/bash
# start-lite.sh - Launch the display on low-RAM boards (Pi Zero 2 W, etc.)
#
# Runs the Flask server (so you can add items from your phone/laptop) AND a
# lightweight pygame fullscreen display drawn straight to the screen.
# No browser, no desktop environment needed.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Python setup ---
# We use the SYSTEM python (not a venv) so it can see the apt-installed pygame,
# which is far lighter than pip-building pygame on a Zero 2 W.
# Install prerequisites once with:
#   sudo apt install -y python3-pygame python3-flask
PYTHON=python3

# Verify dependencies are present, with a helpful message if not.
if ! $PYTHON -c "import flask" 2>/dev/null; then
    echo "ERROR: Flask not found. Install it with:"
    echo "  sudo apt install -y python3-flask"
    exit 1
fi
if ! $PYTHON -c "import pygame" 2>/dev/null; then
    echo "ERROR: pygame not found. Install it with:"
    echo "  sudo apt install -y python3-pygame"
    exit 1
fi

# --- Stop any existing instance ---
pkill -f "app.py" 2>/dev/null || true
pkill -f "tracker.py" 2>/dev/null || true
pkill -f "display.py" 2>/dev/null || true
sleep 1

# --- Start the Flask list server in the background ---
echo "Starting list server..."
$PYTHON app.py &
SERVER_PID=$!

# Wait for the list server to be ready
echo "Waiting for server..."
for i in $(seq 1 20); do
    if curl -s http://localhost:5000 > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# --- Start the time-tracker service (port 5050) in the background ---
# It runs even without tracker_config.py (notifications just stay disabled).
echo "Starting time tracker..."
$PYTHON tracker.py &
TRACKER_PID=$!

# --- Start the fullscreen display ---
# If a desktop (X) is running, use it. Otherwise pygame draws to the framebuffer.
echo "Starting display..."
if [ -n "$DISPLAY" ] || [ -S /tmp/.X11-unix/X0 ]; then
    DISPLAY="${DISPLAY:-:0}" ROTATE="${ROTATE:-0}" $PYTHON display.py &
else
    # No X server: use the Linux framebuffer directly (console mode).
    SDL_VIDEODRIVER=fbcon SDL_FBDEV=/dev/fb0 ROTATE="${ROTATE:-0}" $PYTHON display.py &
fi
DISPLAY_PID=$!

PI_IP=$(hostname -I | awk '{print $1}')
echo "Running. Server PID: $SERVER_PID, Tracker PID: $TRACKER_PID, Display PID: $DISPLAY_PID"
echo "Lists:        http://$PI_IP:5000"
echo "Time tracker: http://$PI_IP:5050"
echo "To stop: ./stop.sh  (or press Esc/Q on the Pi)"

# --- Auto-update loop (Option 1: self-contained, no cron) ---
# Every UPDATE_INTERVAL seconds, check GitHub for new code. If the commit
# changed, pull it and restart the display by re-launching this script.
# Disable by running with AUTO_UPDATE=0.
AUTO_UPDATE="${AUTO_UPDATE:-1}"
UPDATE_INTERVAL="${UPDATE_INTERVAL:-600}"   # 600s = 10 minutes
LOG="$SCRIPT_DIR/update.log"

update_loop() {
    set +e   # tolerate transient failures (e.g. no network during a fetch)
    while true; do
        sleep "$UPDATE_INTERVAL"
        # Trim the log so it never grows unbounded.
        if [ -f "$LOG" ]; then
            tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
        fi
        echo "----- $(date) check -----" >> "$LOG"

        # Fetch only (cheap) to see if the remote moved.
        git fetch >> "$LOG" 2>&1
        LOCAL=$(git rev-parse HEAD 2>/dev/null)
        REMOTE=$(git rev-parse '@{u}' 2>/dev/null)

        if [ -z "$REMOTE" ] || [ "$LOCAL" = "$REMOTE" ]; then
            echo "No changes." >> "$LOG"
            continue
        fi

        echo "New version found ($LOCAL -> $REMOTE)." >> "$LOG"

        # What files changed between our version and the new one?
        CHANGED=$(git diff --name-only "$LOCAL" "$REMOTE" 2>/dev/null)
        echo "Changed files:" >> "$LOG"
        echo "$CHANGED" >> "$LOG"

        # Pull the new code.
        git reset --hard >> "$LOG" 2>&1
        git pull >> "$LOG" 2>&1

        # Decide whether a restart is needed. Only files that actually RUN on
        # the Pi require restarting the display/services. Phone-only files
        # (scriptable/, README, images) are pulled quietly with no restart.
        # Pattern matches the files/dirs that affect the running display.
        if echo "$CHANGED" | grep -Eq '^(display\.py|tracker\.py|app\.py|start-lite\.sh|stop\.sh|templates/|static/|tracker_config\.py|requirements\.txt)'; then
            echo "Runtime files changed - restarting display." >> "$LOG"
            DISPLAY="${DISPLAY:-:0}" ROTATE="${ROTATE:-0}" \
                nohup bash "$SCRIPT_DIR/start-lite.sh" >> "$LOG" 2>&1 &
            # Stop this (old) instance; the new one takes over.
            kill "$SERVER_PID" "$TRACKER_PID" "$DISPLAY_PID" 2>/dev/null
            exit 0
        else
            echo "Only non-runtime files changed - pulled, no restart." >> "$LOG"
        fi
    done
}

if [ "$AUTO_UPDATE" = "1" ]; then
    update_loop &
    UPDATE_PID=$!
    echo "Auto-update every ${UPDATE_INTERVAL}s (PID $UPDATE_PID)."
fi

# Clean up all child processes when this script is stopped.
cleanup() {
    kill "$UPDATE_PID" 2>/dev/null
    kill "$SERVER_PID" "$TRACKER_PID" "$DISPLAY_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

# Keep the script alive while the server runs
wait $SERVER_PID
