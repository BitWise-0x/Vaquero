#!/usr/bin/env bash
# Builds the DMG for the release at HEAD and attaches it to that GitHub release, where Check for
# updates finds it. Run after the Release workflow has tagged and pushed: git pull, then ./release-app.sh.
set -euo pipefail
cd "$(dirname "$0")"
[ -z "$(git status --porcelain)" ] || { echo "working tree has changes; commit or stash them first" >&2; exit 1; }
git fetch --quiet --tags origin
tag="$(git describe --exact-match --tags HEAD 2>/dev/null)" || { echo "HEAD is not a release tag; git pull first" >&2; exit 1; }
version="$(uv run python -c 'import facesort; print(facesort.__version__)')"
[ "$tag" = "v$version" ] || { echo "tag $tag does not match version $version" >&2; exit 1; }
./build.sh
gh release upload "$tag" "dist/FaceSort-$version-arm64.dmg" --clobber
echo "attached FaceSort-$version-arm64.dmg to $tag"
