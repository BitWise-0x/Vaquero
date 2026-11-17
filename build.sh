#!/usr/bin/env bash
# Builds dist/FaceSort.app. Models are not bundled: the app downloads its pack on first launch.
set -euo pipefail
cd "$(dirname "$0")"
uv sync
uv run pyinstaller --noconfirm --windowed --name FaceSort \
  --collect-binaries onnxruntime \
  --exclude-module tkinter \
  facesort/__main__.py
version="$(uv run python -c 'import facesort; print(facesort.__version__)')"
plist=dist/FaceSort.app/Contents/Info.plist
plutil -replace CFBundleShortVersionString -string "$version" "$plist"
plutil -replace CFBundleVersion -string "$version" "$plist"
echo "built: $(pwd)/dist/FaceSort.app ($version)"
