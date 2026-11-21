#!/usr/bin/env bash
# Publishes a face model pack zip as its own release on the Vaquero repo, then prints the MODEL_ARCHIVE pin for
# vaquero/remote.py. Model releases use a model-* tag and are never marked latest; the app's update check reads only
# vX.Y.Z releases. A new or updated model gets a new tag; the app uses it once the pin in remote.py points at it.
#   usage: scripts/publish_model.sh <pack.zip> <tag> [upstream-url]
#   e.g.   scripts/publish_model.sh buffalo_l.zip model-buffalo_l-1 \
#            https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip
set -euo pipefail
zip=${1:?pack zip}
tag=${2:?tag, model-<pack>-<n>}
upstream=${3:-}
repo=BitWise-0x/Vaquero

[[ $tag == model-* ]] || { echo "tag must start with model-" >&2; exit 1; }
names=$(unzip -Z1 "$zip")
grep -qE '(^|/)(det_|scrfd)[^/]*\.onnx$' <<<"$names" || { echo "$zip holds no detector (det_*.onnx, scrfd*.onnx)" >&2; exit 1; }
grep -qE '(^|/)(w600k|glint|arcface)[^/]*\.onnx$' <<<"$names" || { echo "$zip holds no recognizer" >&2; exit 1; }

size=$(wc -c <"$zip" | tr -d " ")
sha=$(shasum -a 256 "$zip" | cut -d' ' -f1)
name=$(basename "$zip")
notes="Face model pack \`$name\` for Vaquero, unmodified.

- Size: $size bytes
- SHA-256: \`$sha\`${upstream:+
- Upstream: $upstream}

InsightFace pretrained models are licensed for non-commercial research use only. They are not part of Vaquero and
are not covered by its MIT license."

gh release create "$tag" "$zip" --repo "$repo" --latest=false --title "Face model: ${name%.zip} ($tag)" --notes "$notes"

published=$(gh release view "$tag" --repo "$repo" --json assets --jq ".assets[] | select(.name == \"$name\") | .digest")
[[ $published == "sha256:$sha" ]] || { echo "GitHub reports $published for $name, expected sha256:$sha" >&2; exit 1; }

cat <<EOF

MODEL_ARCHIVE pin for vaquero/remote.py:
    name="$name",
    size=$size,
    url="https://github.com/$repo/releases/download/$tag/$name",
    digest="sha256:$sha",
EOF
