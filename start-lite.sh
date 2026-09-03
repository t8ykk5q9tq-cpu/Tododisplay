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
pkill -f "display.py" 2>/dev/null || true
sleep 1

# --- Start the Flask server in the background ---
echo "Starting server..."
$PYTHON app.py &
SERVER_PID=$!

# Wait for the server to be ready
echo "Waiting for server..."
for i in $(seq 1 20); do
    if curl -s http://localhost:5000 > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

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

echo "Running. Server PID: $SERVER_PID, Display PID: $DISPLAY_PID"
echo "Add items from any device at: http://$(hostname -I | awk '{print $1}'):5000"
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

        BEFORE=$(git rev-parse HEAD 2>/dev/null)
        # Fetch only (cheap) to see if remote moved, then pull if so.
        git fetch >> "$LOG" 2>&1
        LOCAL=$(git rev-parse HEAD 2>/dev/null)
        REMOTE=$(git rev-parse '@{u}' 2>/dev/null)

        if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
            echo "New version found ($LOCAL -> $REMOTE). Updating." >> "$LOG"
            git reset --hard >> "$LOG" 2>&1
            git pull >> "$LOG" 2>&1
            echo "Restarting display with new code." >> "$LOG"
            # Relaunch this script (it kills existing instances on startup),
            # preserving the current environment (ROTATE, etc.).
            DISPLAY="${DISPLAY:-:0}" ROTATE="${ROTATE:-0}" \
                nohup bash "$SCRIPT_DIR/start-lite.sh" >> "$LOG" 2>&1 &
            # Stop this (old) instance; the new one takes over.
            kill "$SERVER_PID" "$DISPLAY_PID" 2>/dev/null
            exit 0
        else
            echo "No changes." >> "$LOG"
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
    kill "$SERVER_PID" "$DISPLAY_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

# Keep the script alive while the server runs
wait $SERVER_PID
