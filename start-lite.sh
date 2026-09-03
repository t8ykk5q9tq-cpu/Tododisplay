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
    DISPLAY="${DISPLAY:-:0}" $PYTHON display.py &
else
    # No X server: use the Linux framebuffer directly (console mode).
    SDL_VIDEODRIVER=fbcon SDL_FBDEV=/dev/fb0 $PYTHON display.py &
fi
DISPLAY_PID=$!

echo "Running. Server PID: $SERVER_PID, Display PID: $DISPLAY_PID"
echo "Add items from any device at: http://$(hostname -I | awk '{print $1}'):5000"
echo "To stop: ./stop.sh  (or press Esc/Q on the Pi)"

# Keep the script alive while the server runs
wait $SERVER_PID
