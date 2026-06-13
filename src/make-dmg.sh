#!/usr/bin/env bash
# Package ai-limit.app as a distributable DMG.
#
# Default flow, no Apple Developer account required:
#   1. Check that dist/ai-limit.app exists.
#   2. Ad-hoc sign the app so macOS sees a consistent local signature.
#   3. Strip quarantine attributes from the local bundle before packaging.
#   4. Stage ai-limit.app plus an /Applications symlink.
#   5. Create a compressed UDZO DMG at dist/ai-limit-<version>.dmg.
#
# Dependencies: uses the system hdiutil only; no brew create-dmg required.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BUNDLE="dist/ai-limit.app"
VOLNAME="ai-limit"
STAGE=""

info() { printf "  \033[34m•\033[0m %s\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\n\033[31merror:\033[0m %s\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found; cannot continue"
}

cleanup() {
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then
    rm -rf "$STAGE"
  fi
}
trap cleanup EXIT

require_cmd awk
require_cmd codesign
require_cmd du
require_cmd hdiutil
require_cmd plutil
require_cmd xattr

if [[ ! -d "$BUNDLE" ]]; then
  die "$BUNDLE does not exist. Run: .venv/bin/python setup.py py2app"
fi

VERSION=$(plutil -extract CFBundleShortVersionString raw "$BUNDLE/Contents/Info.plist")
DMG_OUT="dist/ai-limit-${VERSION}.dmg"

# 0. Re-sign the entire bundle so the signature is complete and consistent.
#    py2app only performs shallow ad-hoc signing; inconsistent nested signatures
#    can make Apple Silicon report the app as damaged.
#    Without an Apple Developer ID, ad-hoc signing cannot prevent Gatekeeper
#    warnings for downloaded apps. It only avoids broken-signature/damaged-app
#    failures and makes local installs smoother.
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  info "Signing $BUNDLE with Developer ID..."
  codesign --force --deep --options runtime --timestamp \
    --sign "$CODESIGN_IDENTITY" "$BUNDLE"
else
  info "Ad-hoc signing $BUNDLE..."
  codesign --force --deep --sign - "$BUNDLE"
fi
codesign --verify --deep --strict "$BUNDLE"
ok "Code signature verified"

# 1. Strip local quarantine metadata before creating the DMG. This cannot stop
#    browsers/GitHub from adding quarantine to a downloaded DMG later, but it
#    keeps locally copied/tested builds clean.
info "Removing local quarantine attributes..."
xattr -dr com.apple.quarantine "$BUNDLE" 2>/dev/null || true

STAGE=$(mktemp -d -t ai-limit-dmg)

# 2. Stage the DMG layout.
info "Staging DMG layout..."
cp -R "$BUNDLE" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# 3. Create the DMG (UDZO = compressed read-only distribution format).
info "Creating $DMG_OUT..."
rm -f "$DMG_OUT"
hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG_OUT" >/dev/null
ok "DMG created"

# 4. Optional notarization. Requires an Apple Developer account.
#    First store a keychain profile with `xcrun notarytool store-credentials`,
#    then run CODESIGN_IDENTITY=... NOTARY_PROFILE=... ./make-dmg.sh.
if [[ -n "${CODESIGN_IDENTITY:-}" && -n "${NOTARY_PROFILE:-}" ]]; then
  require_cmd xcrun
  info "Submitting for notarization with notarytool..."
  xcrun notarytool submit "$DMG_OUT" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_OUT"
  ok "Notarization complete and stapled"
else
  warn "DMG is ad-hoc signed but not notarized"
  warn "Downloaded copies may still require Open Anyway or quarantine removal"
fi

# 5. Summary
SIZE=$(du -h "$DMG_OUT" | awk '{print $1}')
ok "DMG generated: $DMG_OUT ($SIZE)"
printf "\n"
printf "User installation steps:\n"
printf "  1. Double-click %s to mount it\n" "$DMG_OUT"
printf "  2. Drag ai-limit.app into the Applications folder\n"
if [[ -z "${NOTARY_PROFILE:-}" ]]; then
  printf "  3. If Gatekeeper blocks first launch because the app is not notarized:\n"
  printf "     System Settings -> Privacy & Security -> Open Anyway\n"
  printf "     Or run: xattr -dr com.apple.quarantine /Applications/ai-limit.app\n"
  printf "\n"
  printf "The installer removes quarantine from /Applications/ai-limit.app after copying.\n"
fi
