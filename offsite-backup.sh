#!/bin/bash
# offsite-backup.sh - Copy the Pi's backups/ folder to another machine over
# the network (e.g. your Mac over Tailscale), so a dead SD card can't take
# both the live data AND its local backups at once.
#
# Runs ON THE PI. It rsyncs backups/ -> OFFSITE_DEST via SSH. The tracker
# calls this automatically right after each nightly snapshot (see run_backup
# in tracker.py); you can also run it by hand:
#
#   bash offsite-backup.sh
#
# Configure the destination with the OFFSITE_DEST env var (user@host:/path).
# Point it at your Mac's Tailscale name or IP, e.g.:
#
#   OFFSITE_DEST="matthewlabuzzetta@100.x.y.z:/Users/matthewlabuzzetta/tododisplay-backups"
#
# For unattended nightly runs, set up SSH key auth from the Pi to the Mac
# (ssh-keygen on the Pi, then ssh-copy-id matthewlabuzzetta@<mac-tailscale-ip>)
# so rsync doesn't prompt for a password. Make sure "Remote Login" is enabled
# on the Mac (System Settings > General > Sharing > Remote Login).

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/backups/"

# Destination: user@host:/path on the Mac (over Tailscale). Empty = disabled.
OFFSITE_DEST="${OFFSITE_DEST:-}"

if [ -z "$OFFSITE_DEST" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite-backup: OFFSITE_DEST not set; skipping."
    echo "  Set it, e.g.: OFFSITE_DEST=\"user@100.x.y.z:/Users/you/tododisplay-backups\""
    exit 0
fi

if [ ! -d "$SRC" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite-backup: no backups/ folder yet; nothing to copy."
    exit 0
fi

# -a archive, -z compress, --delete so the mirror matches (old pruned snapshots
# get removed off-site too). BatchMode+timeout so an unattended run can't hang.
rsync -az --delete \
    -e "ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new" \
    "$SRC" "$OFFSITE_DEST/"
status=$?

if [ "$status" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite-backup: mirrored backups/ -> ${OFFSITE_DEST}"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite-backup: FAILED (rsync exit ${status}). Check SSH key auth / Mac reachable on Tailscale."
fi
exit "$status"
