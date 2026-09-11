#!/bin/bash
# mac_usage.sh - Track active time per app on this Mac, sent to the Pi tracker.
#
# Every minute it checks:
#   - the frontmost (active) application
#   - whether you were active (mouse/keyboard used within the last 60s)
# If active, it logs one "active minute" to the tracker under "Mac:<AppName>",
# keeping Mac usage separate from your phone app data.
#
# Run it in the background on your Mac:
#   nohup bash mac_usage.sh > /tmp/mac_usage.log 2>&1 &
# Or set it as a login item (see README notes).
#
# Requires: Tailscale on (or same LAN as the Pi). No special permissions.

# Pi tracker base URL (Tailscale address).
TRACKER_URL="${TRACKER_URL:-http://100.102.96.42:5050}"
IDLE_LIMIT=60   # seconds; considered "active" if last input was within this

# URL-encode helper (spaces etc. in app names).
urlencode() {
    local s="$1" out="" c
    for (( i=0; i<${#s}; i++ )); do
        c="${s:$i:1}"
        case "$c" in
            [a-zA-Z0-9.~_-]) out+="$c" ;;
            *) printf -v c '%%%02X' "'$c"; out+="$c" ;;
        esac
    done
    printf '%s' "$out"
}

frontmost_app() {
    osascript -e 'tell application "System Events" to name of first application process whose frontmost is true' 2>/dev/null
}

idle_seconds() {
    # HIDIdleTime is nanoseconds since last mouse/keyboard input.
    ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}'
}

echo "Mac usage tracker started -> $TRACKER_URL"
while true; do
    idle=$(idle_seconds)
    # Default to "active" if we couldn't read idle time, rather than lose data.
    if [ -z "$idle" ] || [ "$idle" -lt "$IDLE_LIMIT" ]; then
        app=$(frontmost_app)
        if [ -n "$app" ]; then
            enc=$(urlencode "Mac:$app")
            curl -s -m 5 "$TRACKER_URL/activeminute?app=$enc" > /dev/null 2>&1 \
                && echo "$(date '+%H:%M') active in: $app" \
                || echo "$(date '+%H:%M') could not reach tracker"
        fi
    else
        echo "$(date '+%H:%M') idle (${idle}s) - not logging"
    fi
    sleep 60
done
