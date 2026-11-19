#!/usr/bin/env bash
# Builds dist/Vaquero.app and dist/Vaquero-<version>-arm64.dmg (Apple Silicon only: onnxruntime
# ships no Intel macOS wheels). Models are not bundled: the app downloads its pack on first launch.
# Both are signed with the Developer ID identity, notarized and stapled.
#   VAQUERO_SIGN_IDENTITY   codesign identity, name or SHA-1 (default: the team's Vaquero certificate)
#   VAQUERO_NOTARY_KEY      App Store Connect API key (.p8) path, with VAQUERO_NOTARY_KEY_ID and
#                           VAQUERO_NOTARY_ISSUER; without it, the notarytool keychain profile
#                           VAQUERO_NOTARY_PROFILE (default: vaquero-notary)
set -euo pipefail
cd "$(dirname "$0")"
identity="${VAQUERO_SIGN_IDENTITY:-AEDC2D68E14271403956FF511A3B3C1DEB1D0343}"
if [ -n "${VAQUERO_NOTARY_KEY:-}" ]; then
  notary=(--key "$VAQUERO_NOTARY_KEY" --key-id "${VAQUERO_NOTARY_KEY_ID:?}" --issuer "${VAQUERO_NOTARY_ISSUER:?}")
else
  notary=(--keychain-profile "${VAQUERO_NOTARY_PROFILE:-vaquero-notary}")
fi
security find-identity -v -p codesigning | grep -qF "$identity" || { echo "no valid signing identity: $identity" >&2; exit 1; }

sign() { codesign --force --timestamp --options runtime --sign "$identity" "$@"; }

# submits a file to Apple's notary service and fails with the notary log unless it is accepted
notarize() {
  local out id status
  out="$(xcrun notarytool submit "$1" "${notary[@]}" --wait --output-format plist)"
  id="$(plutil -extract id raw -o - - <<<"$out")"
  status="$(plutil -extract status raw -o - - <<<"$out")"
  if [ "$status" != Accepted ]; then
    xcrun notarytool log "$id" "${notary[@]}" >&2 || true
    echo "notarization of $1: $status" >&2
    exit 1
  fi
}
uv sync
version="$(uv run python -c 'import vaquero; print(vaquero.__version__)')"
rm -rf build dist/Vaquero dist/Vaquero.app dist/dmg
uv run pyinstaller --noconfirm --windowed --name Vaquero \
  --icon assets/Vaquero.icns \
  --osx-bundle-identifier com.bitwise0x.vaquero \
  --target-architecture arm64 \
  --collect-binaries onnxruntime \
  --exclude-module tkinter \
  --add-data assets/Vaquero.png:assets \
  --add-data assets/check.svg:assets \
  --add-data assets/buffalo.png:assets \
  --add-data LICENSE:. \
  --add-data THIRD_PARTY_NOTICES.md:. \
  vaquero/__main__.py
plist=dist/Vaquero.app/Contents/Info.plist
plutil -replace CFBundleShortVersionString -string "$version" "$plist"
plutil -replace CFBundleVersion -string "$version" "$plist"
plutil -replace LSMinimumSystemVersion -string "14.0" "$plist"
# the reasons macOS shows when asking for access to these places
access="Vaquero reads photos and videos there to find the person you picked, and copies or moves the matches."
for key in NSRemovableVolumesUsageDescription NSNetworkVolumesUsageDescription NSDocumentsFolderUsageDescription \
  NSDesktopFolderUsageDescription NSDownloadsFolderUsageDescription; do
  plutil -replace "$key" -string "$access" "$plist"
done

# sign inside out: every Mach-O file and nested bundle, deepest first, then the app itself
app=dist/Vaquero.app
while IFS= read -r path; do
  sign "$path"
done < <(
  {
    find "$app/Contents" -type f -print0 | xargs -0 file --no-pad --separator '|' | awk -F'|' '/Mach-O/ {print $1}'
    find "$app/Contents" -type d \( -name '*.framework' -o -name '*.app' \)
  } | awk -F/ '{print NF "\t" $0}' | sort -rns -k1,1 | cut -f2-
)
sign "$app"
codesign --verify --deep --strict "$app"
ditto -c -k --keepParent "$app" dist/Vaquero-notarize.zip
notarize dist/Vaquero-notarize.zip
rm dist/Vaquero-notarize.zip
xcrun stapler staple -q "$app"

# the drag-to-Applications disk image
dmg="dist/Vaquero-$version-arm64.dmg"
mkdir -p dist/dmg
ditto dist/Vaquero.app dist/dmg/Vaquero.app
ln -s /Applications dist/dmg/Applications
cp assets/Vaquero.icns dist/dmg/.VolumeIcon.icns
rm -f "$dmg"
# the volume's custom-icon flag does not survive -srcfolder: build writable, flag the mounted root, then compress
rw=dist/Vaquero-rw.dmg
mnt=dist/dmg-mnt
rm -f "$rw"
hdiutil create -quiet -volname "Vaquero $version" -srcfolder dist/dmg -fs HFS+ -format UDRW "$rw"
mkdir -p "$mnt"
hdiutil attach "$rw" -nobrowse -mountpoint "$mnt" -quiet
SetFile -a C "$mnt"
hdiutil detach "$mnt" -quiet
hdiutil convert "$rw" -quiet -format UDZO -o "$dmg"
codesign --force --timestamp --sign "$identity" "$dmg"
notarize "$dmg"
xcrun stapler staple -q "$dmg"
rm -rf build dist/dmg "$mnt" "$rw" dist/Vaquero Vaquero.spec
echo "built: $(pwd)/dist/Vaquero.app and $(pwd)/$dmg ($version)"
