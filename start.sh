#!/bin/bash
# start.sh - Launch the Todo Display on a Raspberry Pi
# This script starts the Flask server and opens Chromium in kiosk mode.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
source venv/bin/activate
pip install -q -r requirements.txt

# Kill any existing instance
pkill -f "app.py" 2>/dev/null || true
pkill -f "chromium-browser.*localhost:5000" 2>/dev/null || true

# Enable live reload by running: LIVE_RELOAD=1 ./start.sh
# In live-reload mode the server restarts on Python changes and the browser
# refreshes automatically when frontend files change.
if [ "${LIVE_RELOAD:-0}" = "1" ]; then
    echo "Live reload ENABLED"
fi

# Start the Flask server in the background
echo "Starting server..."
LIVE_RELOAD="${LIVE_RELOAD:-0}" python3 app.py &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for server..."
for i in $(seq 1 20); do
    if curl -s http://localhost:5000 > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Detect the Chromium command (varies by OS version):
#   - Raspberry Pi OS Bookworm and newer: "chromium"
#   - Older versions:                      "chromium-browser"
if command -v chromium >/dev/null 2>&1; then
    CHROMIUM="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM="chromium-browser"
else
    echo "ERROR: Chromium is not installed."
    echo "Install it with: sudo apt install -y chromium-browser"
    echo "(The server is still running at http://localhost:5000 — you can open it in any browser.)"
    CHROMIUM=""
fi

# Launch Chromium in kiosk mode (fullscreen, no UI)
if [ -n "$CHROMIUM" ]; then
    echo "Launching display with $CHROMIUM..."
    DISPLAY=:0 "$CHROMIUM" \
        --noerrdialogs \
        --disable-infobars \
        --kiosk \
        --incognito \
        --disable-translate \
        --disable-features=TranslateUI \
        --overscroll-history-navigation=0 \
        --disable-pinch \
        http://localhost:5000 &
fi

echo "Display is running. Server PID: $SERVER_PID"
echo "To stop: ./stop.sh"

wait $SERVER_PID
