<h1 align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Qt-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="Qt"/>
  <img src="https://img.shields.io/badge/ONNX_Runtime-005CED?style=for-the-badge&logo=onnx&logoColor=white" alt="ONNX Runtime"/>
  <img src="https://img.shields.io/badge/Apple_Silicon-000000?style=for-the-badge&logo=apple&logoColor=white" alt="Apple Silicon"/>
  <br>
  <br>
  <code><strong>FaceSort</strong></code>
</h1>

<p align="center">
  <img src="https://img.shields.io/badge/versioning-semantic--release-e10079?style=flat-square&logo=semantic-release" alt="semantic-release"/>
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey?style=flat-square&logo=apple" alt="macOS"/>
  <img src="https://img.shields.io/badge/inference-Core_ML-blue?style=flat-square" alt="Core ML"/>
  <img src="https://img.shields.io/badge/models-InsightFace_buffalo__l-orange?style=flat-square" alt="InsightFace buffalo_l"/>
</p>

<p align="center">
  Find every photo and video of one person in a folder and move them out, by face.<br>
  Single-window macOS app running SCRFD detection and ArcFace recognition on the Apple Neural Engine through Core ML.
</p>

---

## Quick Start

```sh
uv sync
mkdir -p models/buffalo_l   # then add det_10g.onnx and w600k_r50.onnx, see Models
uv run facesort
```

Pick a folder of reference photos, a folder to scan and a destination, then **Scan**. Review the match count and log, adjust the threshold if needed, then **Execute move**.

<br>

## Features

- **Seeded matching**: a handful of photos of one person is enough; no training step
- **Outlier-proof references**: a reference photo whose largest face is someone else is detected and dropped automatically
- **Photos and videos**: videos are judged on evenly sampled frames (10 by default, adjustable)
- **Optional subfolder scan**: off by default; hidden folders and the destination are always skipped
- **Live threshold**: changing the match threshold after a scan updates the match count without rescanning
- **Lookalike flagging**: matches below 0.45 are logged as `review`, with how many reference photos each one agrees with
- **Tight crops handled**: a face filling the whole frame, which the detector misses at normal scale, is found on a padded retry
- **Name conflicts**: when the destination already has the name, keep both (Finder-style `name 2.jpg`) or overwrite, set under **Settings**; every move is journaled for undo
- **Progress and cancel**: scan and move both run in the background with a progress bar, a DONE or CANCELLED status line, and a Cancel that stops cleanly after the current file
- **Readable logs**: color-coded log view (matches, warnings, errors, completions), and a fresh log file for every scan
- **Apple Silicon native**: Core ML execution on the Neural Engine and GPU, with parallel file decoding feeding the model
- **Model packs**: any InsightFace pack dropped into `models/` appears in the Model dropdown
- **Standalone app**: `build.sh` produces `FaceSort.app` with the models bundled inside

<br>

## Architecture

```mermaid
graph TD
    ui["<b>Window</b><br>PySide6<br>folders · options · log"]

    subgraph Worker thread
        worker["<b>ScanWorker</b><br>QThread · cancellable"]
        mover["<b>MoveWorker</b><br>QThread · cancellable<br>journaled moves"]
        prefetch["<b>Prefetch pool</b><br>4 threads<br>image decode · video frame sampling"]
        matcher["<b>FaceMatcher</b><br>reference centroid<br>cosine scoring"]
    end

    subgraph Core ML via ONNX Runtime
        det["<b>SCRFD</b><br>det_10g.onnx<br>faces + 5 landmarks"]
        rec["<b>ArcFace</b><br>w600k_r50.onnx<br>512-d embedding"]
    end

    disk[("<b>Next to the app</b><br>facesort-*.log<br>facesort-scan-*.csv<br>facesort-moves.csv")]

    ui -->|"Scan"| worker
    worker --> prefetch
    prefetch -->|"decoded frames"| matcher
    matcher --> det
    det -->|"aligned 112x112 crop"| rec
    rec -->|"embedding"| matcher
    worker -->|"progress · results"| ui
    ui -->|"Execute move"| mover
    mover -->|"progress"| ui
    mover --> disk
    worker --> disk
```

<br>

## How It Works

```mermaid
flowchart LR
    refs["Reference<br>photos"] --> largest["Largest face<br>per photo"]
    largest --> centroid["Mean<br>embedding"]
    centroid --> outlier{"Each ref within<br>0.4 of mean?"}
    outlier -->|"no"| drop["Dropped<br>(logged)"]
    outlier -->|"yes"| final["Reference<br>centroid"]

    files["Files to scan"] --> detect["Detect faces"]
    detect -->|"none found"| pad["Retry with<br>25% border"]
    detect --> faces["Every face<br>in file / frames"]
    pad --> faces
    faces --> score["Best cosine<br>similarity"]
    final --> score
    score --> thr{"≥ threshold?"}
    thr -->|"yes"| move["Move to<br>destination"]
    thr -->|"no"| stay["Left in place"]
```

1. **References**: the largest face in each reference photo is embedded with ArcFace. Their mean is the reference centroid; any reference below 0.4 similarity to it is a different person and is dropped.
2. **Detection**: SCRFD finds every face and its five landmarks. When it finds none, it retries once on the image with a 25% border, which brings a face that fills the frame into the size range it detects. Each face is aligned to the ArcFace template and embedded.
3. **Scoring**: a file's score is its best face's cosine similarity to the centroid. Videos take the best across their sampled frames.
4. **Moving**: files at or above the threshold move to the destination. Nothing moves until **Execute move**.

<br>

## Progress, Cancel and Logs

- The progress bar tracks both the scan and the move; the line below it shows the count while running, then **DONE** or **CANCELLED** with a summary.
- **Cancel** stops a scan or a move after the current file. A cancelled scan keeps the results it reached, and its matches can still be moved. A cancelled move leaves every file either fully moved and journaled or untouched.
- Closing the window during a run cancels it and waits up to 30 s for the current file before quitting.
- Each **Scan** clears the log view and starts a new log file; the move that follows writes to the same file. The view colors matches green, review matches and warnings amber, errors red and completions blue; the log files stay plain text.

<br>

## Options

| Option | Default | Description |
|---|---|---|
| Reference faces | | Folder of photos of the person to find |
| Folder to scan | | Folder searched for that person |
| Move matches to | | Destination; created if missing |
| **Settings** button or menu > name conflict | Keep both | A name already in the destination: keep both, the moved file numbered (`IMG_1 2.png`), or overwrite |
| Model | `buffalo_l` | Model pack from `models/` |
| Scan subfolders | off | Also scan subfolders; matches land flat in the destination |
| Include videos | on | Score `.mov`, `.mp4`, `.m4v`, `.avi`, `.mkv` |
| Frames | `10` | Frames sampled per video; scan time per video grows with it |
| Match threshold | `0.35` | Minimum score to count as a match |

Settings persist between launches.

<br>

## Scores

Score is the cosine similarity between ArcFace embeddings, from -1 to 1. On a 3,800-file test library, the person searched for scored 0.35 and up while everyone else scored 0.30 and below, which sets the 0.35 default. Small, blurred, side-on or heavily filtered faces score lower; lower the threshold to catch them, and check the scan CSV around the cutoff.

Scores from 0.35 to 0.45 are where real but hard shots and lookalikes overlap. Those matches are logged as `review` (amber) rather than `match` (green), and the done line counts them. Each match also shows `refs N/M`: how many of the M reference photos its face reaches 0.35 with on its own. A true match usually agrees with most of them (13/13); a lookalike resembles the average face but few individual photos (0/13, 1/13). Raising the threshold drops review matches without a rescan.

<br>

## Output Files

Written next to `FaceSort.app` (the repo root when run from source):

| File | Contents |
|---|---|
| `facesort-<timestamp>.log` | Log of one scan and the move that follows it; each scan starts a new one |
| `facesort-scan-<timestamp>.csv` | Every scanned file with its score, face count and reference agreement (`ref_agree`, `N/M`) |
| `facesort-moves.csv` | `source,destination,action` per moved file, for undo; action is `moved`, `renamed` (kept both) or `overwrote` (the replaced file is gone) |

<br>

## Models

`models/<pack>/` holds InsightFace ONNX packs. It is not committed: the files are large, and the InsightFace pretrained weights are licensed for non-commercial research use only. The app reads models only from this folder (and the copy bundled into the `.app`).

A pack needs a detector (`det_*` or `scrfd*`) and a recognizer (`w600k*` or `glintr*`); other files are ignored.

| Pack | Files | Size | Recognizer |
|---|---|---|---|
| `buffalo_l` | `det_10g.onnx`, `w600k_r50.onnx` | 183 MB | ArcFace R50, WebFace600K |

To set up `buffalo_l`, extract those two files from the InsightFace [`buffalo_l.zip`](https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip) release into `models/buffalo_l/`.

<br>

## Performance

ONNX Runtime's Core ML provider runs both models on the Neural Engine and GPU. Measured on an M1 over 40 photos:

| Execution | Inference time |
|---|---|
| Core ML, NeuralNetwork format | 1.73 s |
| Core ML, MLProgram format | 3.52 s |
| CPU | 6.35 s |

Decoding the same photos took 3.05 s, more than inference, so files decode on a 4-thread prefetch pool ahead of the model.

<br>

## Requirements

- macOS on Apple Silicon
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- An InsightFace model pack in `models/`

<br>

## Development

```sh
uv sync
uv run facesort    # run from source
uv run pytest      # tests
./build.sh         # dist/FaceSort.app with models/ bundled
```

Detection (SCRFD) and recognition (ArcFace) run directly on the ONNX models, with no dependency on the `insightface` Python package.

<br>

## Versioning

Every push to `main` runs `.github/workflows/release.yml`, which uses [python-semantic-release](https://python-semantic-release.readthedocs.io/) to read the [Conventional Commits](https://www.conventionalcommits.org/) since the last tag:

| Commit type | Release |
|---|---|
| `fix:`, `perf:` | patch |
| `feat:` | minor |
| `BREAKING CHANGE:` footer | minor while on 0.x, major after |
| anything else (`docs:`, `chore:`, `test:`, ...) | none |

A release sets the version in `pyproject.toml` and `facesort/__init__.py`, relocks `uv.lock`, updates `CHANGELOG.md`, commits them as `chore(release): X.Y.Z`, tags `vX.Y.Z` and creates the GitHub release. Pull before the next local commit. The app shows its version in **FaceSort > About FaceSort**, in each scan log, and in the `.app` bundle's Info.plist (set by `build.sh`).

<br>

## Troubleshooting

- **"No model pack found"**: `models/` is empty or a pack is missing its detector or recognizer. See [Models](#models).
- **A reference photo was dropped**: its largest face is someone else. Crop it to the right person or remove it.
- **"Reference has no face"**: no face was found even on the padded retry, usually a profile, a heavily obscured face, or a very small one. Replace it with a clearer photo.
- **Expected matches missing**: open the scan CSV, sort by score, and lower the threshold to just below the missed files.
- **Unreadable files**: logged and skipped; the scan continues.
- **"Still finishing the current file"** on close: a long video is mid-read; close again a moment later.

<br>

## Disclaimer

This project is not affiliated with or endorsed by InsightFace or Apple Inc. All product and company names are trademarks of their respective owners.
