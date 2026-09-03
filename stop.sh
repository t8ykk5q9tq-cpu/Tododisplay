#!/bin/bash
# stop.sh - Stop the Todo Display

echo "Stopping display..."
pkill -f "chromium-browser.*localhost:5000" 2>/dev/null || true
pkill -f "python3 app.py" 2>/dev/null || true
echo "Stopped."
