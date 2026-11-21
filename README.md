<p align="center">
  <img src="assets/Vaquero-scan.webp" alt="Vaquero icon: a buffalo inside an amber viewfinder, with a scan line sweeping down" width="120"/>
</p>

<h1 align="center">

  <code><strong>Vaquero</strong></code>
</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Qt-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="Qt"/>
  <img src="https://img.shields.io/badge/ONNX_Runtime-efb146?style=for-the-badge&logo=onnx&logoColor=0c0f0e" alt="ONNX Runtime"/>
  <img src="https://img.shields.io/badge/Core_ML-000000?style=for-the-badge&logo=apple&logoColor=white" alt="Core ML"/>
	<br>
  <img src="https://img.shields.io/github/v/release/BitWise-0x/Vaquero?style=flat-square&color=efb146" alt="Latest release"/>
  <img src="https://img.shields.io/badge/macOS_14+-Apple_Silicon-000000?style=flat-square&logo=apple&logoColor=white" alt="macOS 14 or later on Apple Silicon"/>
  <img src="https://img.shields.io/badge/model-InsightFace_buffalo__l-orange?style=flat-square" alt="InsightFace buffalo_l"/>
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT license"/>
</p>

<p align="center">
  <strong>Find and Round up Photos and Videos by Face.</strong><br><br>
  Vaquero learns a face from a few photos, then finds every photo & video it appears in <br> and organizes them your way. Runs entirely offline. No photo or face ever leaves your Mac.<br>
  Free, open source, no account, 100% private
</p>

<br>

<p align="center">
  <a href="https://github.com/BitWise-0x/Vaquero/releases/latest/download/Vaquero-arm64.dmg"><img src="assets/download-button.png" alt="Download for Apple Silicon Macs" width="160"/></a>
  <br>
  <sub>macOS 14 or later · <a href="#install">install steps</a></sub>
  <br>
  <sub><a href="https://vaquero.bitwise0x.com">vaquero.bitwise0x.com</a></sub>
</p>

<br>

<p align="center">
  <img src="docs/screenshots/scan-and-copy.webp" alt="Vaquero scanning a folder, listing the matches, then copying them" width="760"/>
</p>

---

## What It's For

- **A folder per person**: every photo and video of a family member, a friend or yourself, out of years of backups
- **One person from a shared dump**: an event, a trip or a group chat export
- **Albums and slideshows**: every photo and video of one person
- **Clean-up**: move someone's photos out of a mixed folder, or copy them and leave the originals

Three to five clear photos of the person are enough. No training.

<br>

## Screenshots

| First launch | Scanning |
|---|---|
| <img src="docs/screenshots/first-launch.webp" alt="Face model downloading on first launch" width="420"/> | <img src="docs/screenshots/scanning.webp" alt="A scan in progress" width="420"/> |

| Menu bar | Settings |
|---|---|
| <img src="docs/screenshots/menubar.webp" alt="The menu bar menu during a scan: an amber progress bar and the time left, moving while the menu stays open" width="420"/> | <img src="docs/screenshots/settings.png" alt="Settings" width="420"/> |

| Results | Help |
|---|---|
| <img src="docs/screenshots/results.png" alt="A finished scan with its matches listed" width="420"/> | <img src="docs/screenshots/help.png" alt="Help" width="420"/> |

| Undo | Log links |
|---|---|
| <img src="docs/screenshots/undo.png" alt="Edit > Undo asking before it reverses the last copy" width="420"/> | <img src="docs/screenshots/log-menu.png" alt="Right-click on a file in the log: Open, Show in Finder, Copy Path" width="420"/> |

---

## Privacy

Vaquero runs on an open-source AI model, right on your machine. No photo, face or log leaves it.

<br>

## Install

1. Download [Vaquero-arm64.dmg](https://github.com/BitWise-0x/Vaquero/releases/latest/download/Vaquero-arm64.dmg).
2. Open it and drag **Vaquero** onto **Applications**.
3. Open Vaquero and click **Allow** when macOS asks about a folder.

Requires an Apple Silicon Mac with macOS 14 or later. The first launch downloads the face model (about 290 MB).

Pick the reference photos, a folder to scan and a destination, then **Scan**. Check the matches, choose **Copy** or **Move**, then **Execute**.

<br>

## Features

- **100% private**: everything runs on your own Mac
- **A few photos are enough**: no training step
- **Group photos as references**: Vaquero uses the person common to the photos; a photo of someone else is left out
- **Photos and videos**: JPEG, HEIC, PNG and more; videos are checked on evenly spaced frames (10 by default)
- **Subfolders**: optional; hidden folders and the destination are skipped
- **Live threshold**: change it after a scan and the matches update without rescanning
- **Lookalikes flagged**: borderline matches are marked `review`
- **Copy or move**: with keep-both or overwrite when a name is taken; overwritten files go to the Trash where the volume has one
- **Undo**: **Edit > Undo** puts the last copy or move back
- **Safe copies**: every file is flushed to disk and checked before it takes its place; a full or unplugged drive never leaves a partial file, and a stopped copy or move picks up where it left off
- **Progress and cancel**: progress with time left for the scan, the copy or move, and the model download; cancel and quit ask first while one runs
- **Menu bar and Dock**: in the Dock, the menu bar, or both; the menu bar menu shows progress, Cancel and Undo without opening the window
- **Clickable log**: every file and folder name opens it; Option-click shows it in the Finder
- **Network shares and external drives**: a disconnected drive or a share that stops answering is reported, never waited on
- **Apple Silicon native**: Core ML on the Neural Engine and GPU
- **Updates**: **Vaquero > Check for Updates…**, or automatically at launch; each update is checked for Vaquero's own signature before it installs
- **Built-in help**: the **?** button explains every setting

<br>

## How It Works

```mermaid
flowchart LR
    pick["Pick a few photos<br>of the person"] --> learn["Vaquero learns<br>their face"]
    learn --> scan["It checks every photo<br>and video in a folder"]
    scan --> review["You check<br>the matches"]
    review --> done["Copy, move or organize<br>them as you see fit"]
    classDef default fill:#efb146,stroke:#a96a19,stroke-width:2px,color:#0c0f0e
    classDef quiet fill:#1b221f,stroke:#2e3733,stroke-width:2px,color:#e7ece8
    linkStyle default stroke:#efb146,stroke-width:2px
```

For each photo or video:

```mermaid
flowchart LR
    photo["A photo<br>or video"] --> faces{"Any faces?"}
    faces --> noface["No faces:<br>stays where it is"]
    faces --> same{"Is one of them<br>the person?"}
    same --> someone["Someone else:<br>stays where it is"]
    same --> match["It's them:<br>listed as a match"]
    classDef default fill:#efb146,stroke:#a96a19,stroke-width:2px,color:#0c0f0e
    classDef quiet fill:#1b221f,stroke:#2e3733,stroke-width:2px,color:#e7ece8
    linkStyle default stroke:#efb146,stroke-width:2px
    class noface,someone quiet
```

<br>

## Documentation

Also at [vaquero.bitwise0x.com](https://vaquero.bitwise0x.com/docs/install/)

- [Install](docs/install.md): requirements, first launch, permissions, updates, uninstall
- [Using Vaquero](docs/using.md): options, scores and the threshold, progress, undo, the menu bar and Dock, menus and shortcuts, settings, the log
- [Reference photos](docs/reference-photos.md): what makes a good set, and how the person is chosen
- [Files](docs/files.md): logs, scan reports, the crash and update logs, and the move journal
- [How it works](docs/how-it-works.md): detection, recognition, performance and the face model
- [Troubleshooting](docs/troubleshooting.md)

<br>

## Development

```sh
uv sync
uv run vaquero                        # run from source
uv run pytest                         # tests
uv run python -m vaquero --self-test  # the menu bar, Dock and native libraries, on the real window system
./build.sh                            # signed, notarized app and DMG in dist/, after the app's own self-test
```

The self-test and `build.sh` open a window and a menu bar icon, so they need a logged-in desktop session.

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Detection (SCRFD) and recognition (ArcFace) run directly on the ONNX models.

The website is [site/](site/) (Astro and Starlight); it serves `docs/` under `/docs/`:

```sh
cd site
pnpm install
pnpm dev          # http://localhost:4321
pnpm build        # site/dist
```

<br>

## Versioning

Vaquero follows [semantic versioning](https://semver.org/). Every app release is listed in [CHANGELOG.md](CHANGELOG.md) and on [Releases](https://github.com/BitWise-0x/Vaquero/releases) with its DMG. The face model has a release of its own there, `model-buffalo_l-1`.

<br>

## License

[MIT](LICENSE). The libraries bundled in the app keep their own licenses, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The face model is InsightFace's and licensed for non-commercial research use only.

<br>

## Disclaimer

This project is not affiliated with or endorsed by InsightFace or Apple Inc. All product and company names are trademarks of their respective owners.

<br>

---

<p align="center">
  <sub>A <a href="https://bitwise0x.com"><b>BitWise</b></a> product</sub>
</p>
