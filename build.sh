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
echo "built: $(pwd)/dist/FaceSort.app"
