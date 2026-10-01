#!/usr/bin/env bash
# Install the last device build on the paired iPhone.
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}
APP=build/dd/Build/Products/Debug-iphoneos/PennyLane.app
UDID=${1:-$(xcrun devicectl list devices 2>/dev/null | awk '/available|connected/{print $(NF-3)}' | head -1)}
[[ -n "$UDID" ]] || { echo "no paired iPhone found"; exit 1; }
xcrun devicectl device install app --device "$UDID" "$APP"
xcrun devicectl device process launch --device "$UDID" com.alexhedtke.pennylane
