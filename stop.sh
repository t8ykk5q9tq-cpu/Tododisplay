#!/bin/bash
# stop.sh - Stop the Todo Display (both browser and lite/pygame modes)

echo "Stopping display..."
pkill -f "chromium.*localhost:5000" 2>/dev/null || true
pkill -f "chromium-browser.*localhost:5000" 2>/dev/null || true
pkill -f "display.py" 2>/dev/null || true
pkill -f "app.py" 2>/dev/null || true
echo "Stopped."
