#!/bin/bash
# install-mac-agent.sh - Install the Mac usage tracker as a launchd agent so it
# starts automatically at login and always runs in the background.
#
# Run once:   bash mac/install-mac-agent.sh
# Uninstall:  bash mac/install-mac-agent.sh --uninstall

set -e

LABEL="com.tododisplay.macusage"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$AGENT_DIR/$LABEL.plist"

# Resolve the repo dir (parent of this mac/ folder) and the watcher script.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_DIR/mac_usage.sh"
PLIST_SRC="$REPO_DIR/mac/$LABEL.plist"

if [ "$1" = "--uninstall" ]; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "Uninstalled: $LABEL (agent removed)."
    exit 0
fi

if [ ! -f "$SCRIPT" ]; then
    echo "ERROR: cannot find $SCRIPT"
    exit 1
fi

mkdir -p "$AGENT_DIR"

# Fill the __SCRIPT__ placeholder with the real path and install the plist.
sed "s|__SCRIPT__|$SCRIPT|g" "$PLIST_SRC" > "$PLIST_DEST"

# Reload (unload first in case it's already installed).
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Installed: $LABEL"
echo "  script:  $SCRIPT"
echo "  logs:    /tmp/mac_usage.log"
echo "It now runs at login and in the background. Check: tail -f /tmp/mac_usage.log"
