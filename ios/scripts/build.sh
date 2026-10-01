#!/usr/bin/env bash
# Build Penny Lane for iPhone. Default is an unsigned compile check; --device
# signs for a real iPhone. Regenerates the project first (project.yml is the
# source of truth; anything set in Xcode's GUI is wiped on regenerate).
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}
[[ -f Config/Signing.xcconfig ]] || cp Config/Signing.xcconfig.example Config/Signing.xcconfig
xcodegen generate
if [[ "${1:-}" == "--device" ]]; then
  # Delete the built app so codesign re-runs with a fresh profile (a still-valid
  # cached profile would otherwise be re-shipped with its old expiry).
  rm -rf build/dd/Build/Products/Debug-iphoneos/PennyLane.app
  exec xcodebuild -project PennyLane.xcodeproj -scheme PennyLane \
    -destination 'generic/platform=iOS' -derivedDataPath build/dd \
    -allowProvisioningUpdates CODE_SIGN_STYLE=Automatic build
fi
exec xcodebuild -project PennyLane.xcodeproj -scheme PennyLane \
  -destination 'generic/platform=iOS' -derivedDataPath build/dd \
  CODE_SIGNING_ALLOWED=NO build
