#!/usr/bin/env bash
# Install (or reinstall) the launchd job that runs scripts/pull_backups.sh twice a day on this Mac.
#   bash scripts/install_pull_backups.sh
#   launchctl print gui/$(id -u)/com.spotsignal.pullbackups     # inspect
#   launchctl kickstart gui/$(id -u)/com.spotsignal.pullbackups  # run now
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${SPOTSIGNAL_BACKUP_DIR:-$HOME/Backups/SpotSignal}"
PLIST="$HOME/Library/LaunchAgents/com.spotsignal.pullbackups.plist"
mkdir -p "$DEST" "$(dirname "$PLIST")"
sed -e "s#__REPO__#$REPO#" -e "s#__LOG__#$DEST/pull.log#" "$REPO/deploy/com.spotsignal.pullbackups.plist" > "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/com.spotsignal.pullbackups" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed: $PLIST (runs 11:00 and 20:00 local; log $DEST/pull.log)"
