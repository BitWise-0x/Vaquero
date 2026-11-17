#!/usr/bin/env bash
# Builds dist/FaceSort.app with every model pack in models/ bundled inside it.
set -euo pipefail
cd "$(dirname "$0")"
uv sync
ls models/*/*.onnx >/dev/null 2>&1 || { echo "no model packs in models/" >&2; exit 1; }
uv run pyinstaller --noconfirm --windowed --name FaceSort \
  --collect-binaries onnxruntime \
  --exclude-module tkinter \
  --add-data models:models \
  facesort/__main__.py
version="$(uv run python -c 'import facesort; print(facesort.__version__)')"
plist=dist/FaceSort.app/Contents/Info.plist
plutil -replace CFBundleShortVersionString -string "$version" "$plist"
plutil -replace CFBundleVersion -string "$version" "$plist"
echo "built: $(pwd)/dist/FaceSort.app ($version)"
