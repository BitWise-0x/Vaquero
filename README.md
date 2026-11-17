# FaceSort

Single-window macOS app that finds the photos and videos containing a given person's face and moves them to a folder.

## Use

1. **Reference faces**: a folder of images of the person. The largest face in each image is used; an image whose largest face is someone else is dropped automatically and noted in the log.
2. **Folder to scan**: only files directly in this folder, unless **Scan subfolders** is on. Hidden folders and the destination folder are never scanned. Matches from subfolders all land flat in the destination.
3. **Move matches to**: created if missing.
4. **Model**: an insightface model pack, listed from the packs bundled in the app and any in a `models/` folder next to it.

**Scan** scores every file and moves nothing. Adjusting **Match threshold** after a scan updates the match count without rescanning. **Execute move** moves the files at or above the threshold. A file whose name already exists in the destination is skipped, never overwritten.

Videos are judged on frames sampled evenly across their length, 10 by default. Scan time per video grows with the frame count.

## Scores

Score is the cosine similarity between the best face in a file and the mean reference face (ArcFace embeddings). On a 3,800-file library, matches scored 0.35 and up and strangers 0.30 and below, so 0.35 is the default. Small, blurred, side-on or heavily filtered faces score lower.

## Files written next to the app

| File | Contents |
|---|---|
| `facesort.log` | full run log |
| `facesort-scan-<timestamp>.csv` | every scanned file with score and face count |
| `facesort-moves.csv` | `source,destination` pair per moved file, for undo |

When run from source, these land in the repo root.

## Develop

```sh
uv sync
uv run facesort          # run from source
uv run pytest            # tests
./build.sh               # dist/FaceSort.app, bundling models/
```

## Models

`models/<pack>/` holds insightface ONNX packs and is not committed (large, and the weights are licensed for non-commercial research use). A pack needs a detector (`det_*` or `scrfd*`) and a recognizer (`w600k*` or `glintr*`); other files are ignored.

| Pack | Files | Size | Notes |
|---|---|---|---|
| `buffalo_l` | `det_10g.onnx`, `w600k_r50.onnx` | 183 MB | default; ArcFace R50 on WebFace600K |

`buffalo_l` is a copy of `/Volumes/SSD-USB/ComfyUI-fork/models/insightface/models/buffalo_l` (the two files above only).

Python 3.12, PySide6, onnxruntime with the Core ML provider (Neural Engine and GPU on Apple Silicon; its default NeuralNetwork format benchmarks about 2x faster here than MLProgram and 3.5x faster than CPU), OpenCV. Files decode on a 4-thread prefetch pool because decoding costs more than inference. Detection (SCRFD) and recognition (ArcFace) run straight on the ONNX models, with no insightface package dependency.
