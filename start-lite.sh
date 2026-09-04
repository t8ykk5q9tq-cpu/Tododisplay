#!/bin/bash
# start-lite.sh - Launch the display on low-RAM boards (Pi Zero 2 W, etc.)
#
# Runs the Flask server (so you can add items from your phone/laptop) AND a
# lightweight pygame fullscreen display drawn straight to the screen.
# No browser, no desktop environment needed.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# PID of this main script, so background loops can restart the whole instance.
MAIN_PID=$$

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
            # Stop the ENTIRE old instance so the supervisor doesn't race to
            # relaunch the killed services. Killing the main script triggers its
            # cleanup trap (which kills all children); the fresh instance we
            # just launched with nohup takes over.
            kill "$MAIN_PID" 2>/dev/null
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

# --- Wi-Fi watchdog ---
# Every WATCHDOG_INTERVAL seconds, check connectivity. If it's down, re-kick
# the Wi-Fi connection via nmcli so the Pi recovers without a manual reset.
# Disable with WIFI_WATCHDOG=0.
WIFI_WATCHDOG="${WIFI_WATCHDOG:-1}"
WATCHDOG_INTERVAL="${WATCHDOG_INTERVAL:-120}"   # check every 2 minutes
WIFI_CONN="${WIFI_CONN:-netplan-wlan0-S232 Home Wifi}"
WLOG="$SCRIPT_DIR/watchdog.log"

wifi_watchdog() {
    set +e
    while true; do
        sleep "$WATCHDOG_INTERVAL"
        # Consider us online if we can reach a reliable host. Try a couple.
        if ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1 \
           || ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
            continue  # online, nothing to do
        fi
        # Trim the log so it never grows unbounded.
        if [ -f "$WLOG" ]; then
            tail -n 200 "$WLOG" > "$WLOG.tmp" 2>/dev/null && mv "$WLOG.tmp" "$WLOG"
        fi
        echo "$(date) connectivity down - re-kicking Wi-Fi" >> "$WLOG"
        # Bounce the Wi-Fi connection to force a reconnect.
        nmcli connection up "$WIFI_CONN" >> "$WLOG" 2>&1 \
            || nmcli device connect wlan0 >> "$WLOG" 2>&1
        sleep 10
        if ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1; then
            echo "$(date) recovered" >> "$WLOG"
        else
            echo "$(date) still down after re-kick" >> "$WLOG"
        fi
    done
}

if [ "$WIFI_WATCHDOG" = "1" ]; then
    wifi_watchdog &
    WATCHDOG_PID=$!
    echo "Wi-Fi watchdog every ${WATCHDOG_INTERVAL}s (PID $WATCHDOG_PID)."
fi

# Clean up all child processes when this script is stopped.
cleanup() {
    kill "$UPDATE_PID" "$WATCHDOG_PID" 2>/dev/null
    kill "$SERVER_PID" "$TRACKER_PID" "$DISPLAY_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

# --- Supervisor: keep the three services alive ---
# If any of the server / tracker / display crashes, relaunch just that one.
# Disable with AUTO_RESTART=0.
AUTO_RESTART="${AUTO_RESTART:-1}"

start_display() {
    if [ -n "$DISPLAY" ] || [ -S /tmp/.X11-unix/X0 ]; then
        DISPLAY="${DISPLAY:-:0}" ROTATE="${ROTATE:-0}" $PYTHON display.py &
    else
        SDL_VIDEODRIVER=fbcon SDL_FBDEV=/dev/fb0 ROTATE="${ROTATE:-0}" $PYTHON display.py &
    fi
    DISPLAY_PID=$!
}

if [ "$AUTO_RESTART" = "1" ]; then
    while true; do
        sleep 10
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "$(date) list server died - restarting" >> "$WLOG"
            $PYTHON app.py &
            SERVER_PID=$!
        fi
        if ! kill -0 "$TRACKER_PID" 2>/dev/null; then
            echo "$(date) tracker died - restarting" >> "$WLOG"
            $PYTHON tracker.py &
            TRACKER_PID=$!
        fi
        if ! kill -0 "$DISPLAY_PID" 2>/dev/null; then
            echo "$(date) display died - restarting" >> "$WLOG"
            start_display
        fi
    done
else
    # Auto-restart disabled: just keep the script alive on the server.
    wait $SERVER_PID
fi
