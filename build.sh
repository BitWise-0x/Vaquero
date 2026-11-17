#!/usr/bin/env bash
# Builds dist/FaceSort.app and dist/FaceSort-<version>-arm64.dmg (Apple Silicon only: onnxruntime
# ships no Intel macOS wheels). Models are not bundled: the app downloads its pack on first launch.
set -euo pipefail
cd "$(dirname "$0")"
uv sync
version="$(uv run python -c 'import facesort; print(facesort.__version__)')"
rm -rf build dist/FaceSort dist/FaceSort.app dist/dmg
uv run pyinstaller --noconfirm --windowed --name FaceSort \
  --icon assets/FaceSort.icns \
  --osx-bundle-identifier com.bitwise.facesort \
  --target-architecture arm64 \
  --collect-binaries onnxruntime \
  --exclude-module tkinter \
  --add-data assets/FaceSort.png:assets \
  --add-data assets/check.svg:assets \
  --add-data assets/buffalo.png:assets \
  facesort/__main__.py
plist=dist/FaceSort.app/Contents/Info.plist
plutil -replace CFBundleShortVersionString -string "$version" "$plist"
plutil -replace CFBundleVersion -string "$version" "$plist"
plutil -replace LSMinimumSystemVersion -string "14.0" "$plist"

# the drag-to-Applications disk image
dmg="dist/FaceSort-$version-arm64.dmg"
mkdir -p dist/dmg
ditto dist/FaceSort.app dist/dmg/FaceSort.app
ln -s /Applications dist/dmg/Applications
cp assets/FaceSort.icns dist/dmg/.VolumeIcon.icns
rm -f "$dmg"
# the volume's custom-icon flag does not survive -srcfolder: build writable, flag the mounted root, then compress
rw=dist/FaceSort-rw.dmg
mnt=dist/dmg-mnt
rm -f "$rw"
hdiutil create -quiet -volname "FaceSort $version" -srcfolder dist/dmg -fs HFS+ -format UDRW "$rw"
mkdir -p "$mnt"
hdiutil attach "$rw" -nobrowse -mountpoint "$mnt" -quiet
SetFile -a C "$mnt"
hdiutil detach "$mnt" -quiet
hdiutil convert "$rw" -quiet -format UDZO -o "$dmg"
rm -rf build dist/dmg "$mnt" "$rw" dist/FaceSort FaceSort.spec
echo "built: $(pwd)/dist/FaceSort.app and $(pwd)/$dmg ($version)"
