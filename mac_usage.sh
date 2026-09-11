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
INTERVAL=10       # seconds between samples
IDLE_LIMIT=15     # considered "active" if last input was within this (>= INTERVAL)
FLUSH_EVERY=60    # send accumulated samples to the Pi this often (one write)

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
    # Get the frontmost app's real display name. Many Electron apps (Kiro,
    # VS Code, Slack, etc.) report their process as "Electron", so we resolve
    # the actual name from the app bundle's file path instead.
    osascript <<'EOF' 2>/dev/null
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set procName to name of frontApp
    try
        set appPath to POSIX path of (application file of frontApp as alias)
        -- e.g. ".../Kiro.app/" -> "Kiro"
        set AppleScript's text item delimiters to "/"
        set parts to text items of appPath
        repeat with p in reverse of parts
            if p ends with ".app" then
                set procName to text 1 thru -5 of (p as string)
                exit repeat
            end if
        end repeat
        set AppleScript's text item delimiters to ""
    end try
    return procName
end tell
EOF
}

idle_seconds() {
    # HIDIdleTime is nanoseconds since last mouse/keyboard input.
    ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}'
}

# Accumulate per-app seconds locally, then flush once per FLUSH_EVERY to the Pi
# in a single request (far fewer disk writes on the Pi's SD card).
declare -A tally
elapsed=0

flush() {
    # Build "Mac:App:secs,Mac:App2:secs" from the tally, then POST/GET it.
    local data="" name secs enc
    for name in "${!tally[@]}"; do
        secs="${tally[$name]}"
        [ "$secs" -gt 0 ] || continue
        enc=$(urlencode "Mac:$name")
        data+="${enc}:${secs},"
    done
    if [ -n "$data" ]; then
        if curl -s -m 5 "$TRACKER_URL/activebatch?data=${data%,}" > /dev/null 2>&1; then
            echo "$(date '+%H:%M:%S') flushed: $data"
            tally=()   # clear only on success, so we don't lose unsent data
        else
            echo "$(date '+%H:%M:%S') could not reach tracker (kept tally)"
        fi
    fi
}

# Flush whatever we have if the script is stopped.
trap 'flush; exit 0' INT TERM

echo "Mac usage tracker started -> $TRACKER_URL (sample ${INTERVAL}s, flush ${FLUSH_EVERY}s)"
while true; do
    idle=$(idle_seconds)
    # Default to "active" if we couldn't read idle time, rather than lose data.
    if [ -z "$idle" ] || [ "$idle" -lt "$IDLE_LIMIT" ]; then
        app=$(frontmost_app)
        if [ -n "$app" ]; then
            tally["$app"]=$(( ${tally["$app"]:-0} + INTERVAL ))
        fi
    fi
    sleep "$INTERVAL"
    elapsed=$(( elapsed + INTERVAL ))
    if [ "$elapsed" -ge "$FLUSH_EVERY" ]; then
        flush
        elapsed=0
    fi
done
