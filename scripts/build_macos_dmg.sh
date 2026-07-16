#!/usr/bin/env bash
# Build, sign, notarize, and package Consensus.app into a distributable DMG.
#
# One-time setup:
#   1. Ensure your "Developer ID Application" certificate is in the login
#      keychain (Xcode -> Settings -> Accounts -> Manage Certificates).
#   2. Store notarization credentials (use an app-specific password from
#      https://account.apple.com, and your Team ID from the Apple Developer
#      membership page):
#        xcrun notarytool store-credentials consensus-notary \
#            --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID
#
# Usage:
#   scripts/build_macos_dmg.sh                  # full signed + notarized DMG
#   scripts/build_macos_dmg.sh --skip-notarize  # signed only (local testing)
#
# Environment overrides:
#   CODESIGN_IDENTITY   e.g. "Developer ID Application: Jane Doe (TEAMID123)"
#                       (default: first Developer ID Application in keychain)
#   NOTARY_PROFILE      notarytool keychain profile (default: consensus-notary)
set -euo pipefail

cd "$(dirname "$0")/.."

SKIP_NOTARIZE=0
case "${1:-}" in
    "") ;;
    --skip-notarize) SKIP_NOTARIZE=1 ;;
    *) echo "ERROR: unknown option '${1}'"; exit 1 ;;
esac

VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' consensus/__init__.py)
[[ -n "$VERSION" ]] || { echo "ERROR: could not read version"; exit 1; }
APP="build/macos/dist/Consensus.app"
DMG="build/macos/Consensus-${VERSION}.dmg"
ENTITLEMENTS="packaging/macos/entitlements.plist"
NOTARY_PROFILE="${NOTARY_PROFILE:-consensus-notary}"

notarize() {
    # Capture output and assert the verdict ourselves: some notarytool
    # versions exit 0 even when the submission ends up Invalid, and the
    # alternative failure mode (stapler dying later) hides the real cause.
    local out
    out=$(xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait) || {
        printf '%s\n' "$out"
        echo "ERROR: notarytool submit failed for $1"
        exit 1
    }
    printf '%s\n' "$out"
    if ! grep -q "status: Accepted" <<< "$out"; then
        local sub_id
        sub_id=$(awk '/id: /{print $2; exit}' <<< "$out")
        echo "ERROR: notarization of $1 was not accepted; notary log follows."
        [[ -z "$sub_id" ]] || xcrun notarytool log "$sub_id" \
            --keychain-profile "$NOTARY_PROFILE" || true
        exit 1
    fi
}

# --- 0. Icon (generate if missing) ------------------------------------------
[[ -f packaging/macos/Consensus.icns ]] || packaging/macos/make_icns.sh

# --- 1. Build the .app in a fresh venv ---------------------------------------
rm -rf build/macos .build-venv
uv venv .build-venv
uv pip install --python .build-venv/bin/python . pyinstaller
.build-venv/bin/python -m PyInstaller --noconfirm \
    --distpath build/macos/dist --workpath build/macos/work \
    packaging/macos/consensus.spec
[[ -d "$APP" ]] || { echo "ERROR: $APP was not produced"; exit 1; }

# --- 2. Codesign inside-out ---------------------------------------------------
if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
    # Capture first, then scan without an early-exit consumer: piping the
    # producer into `head -1` can SIGPIPE it under pipefail (same bug class
    # as the release script's wheel check).
    IDENTITIES=$(security find-identity -v -p codesigning)
    CODESIGN_IDENTITY=$(awk -F'"' '/Developer ID Application/ && !found {print $2; found=1}' <<< "$IDENTITIES")
fi
[[ -n "$CODESIGN_IDENTITY" ]] || {
    echo "ERROR: no 'Developer ID Application' identity in keychain."
    echo "Set CODESIGN_IDENTITY or install your certificate via Xcode."
    exit 1
}
echo "Signing with: $CODESIGN_IDENTITY"

# Nested binaries first (no entitlements on these). Match by file type,
# not extension, so extensionless Mach-O helpers some wheels ship are
# covered too; the two main executables get re-signed with entitlements
# below, which is fine (--force overwrites).
MACHO_FILES=$(find "$APP" -type f -exec file {} + | awk -F': ' '/Mach-O/ {print $1}')
while IFS= read -r bin; do
    [[ -n "$bin" ]] || continue
    codesign --force --timestamp --options runtime \
        --sign "$CODESIGN_IDENTITY" "$bin"
done <<< "$MACHO_FILES"
# ... then the auxiliary executable, then the bundle (signs the main exe).
codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" \
    "$APP/Contents/MacOS/consensus-worker"
codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" "$APP"
codesign --verify --strict --deep "$APP"
echo "Codesign OK"

# --- 3. Notarize + staple the app --------------------------------------------
if [[ $SKIP_NOTARIZE -eq 0 ]]; then
    ditto -c -k --keepParent "$APP" build/macos/Consensus.zip
    notarize build/macos/Consensus.zip
    xcrun stapler staple "$APP"
fi

# --- 4. Build the DMG ---------------------------------------------------------
STAGE="build/macos/dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Consensus" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

# --- 5. Sign, notarize, staple the DMG ----------------------------------------
codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
if [[ $SKIP_NOTARIZE -eq 0 ]]; then
    notarize "$DMG"
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    spctl -a -vv "$APP"
fi

echo "Done: $DMG"
