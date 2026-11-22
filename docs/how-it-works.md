---
title: How it works
description: Face detection and recognition on your Mac, the face model, and performance.
---

## Privacy

Everything runs on your Mac. No photo, face or log leaves it. The network is used only to download the face model once and to check for updates.

## The scan

1. **References**: every face in the reference photos is found and turned into an embedding, a list of numbers that describes a face. The person common to the photos is chosen, photos of anyone else are dropped, and the average of the rest is the reference face. See [Reference photos](reference-photos.md#how-the-person-is-chosen).
2. **Detection**: in each photo, or each sampled video frame, SCRFD finds every face and five landmarks (eyes, nose, mouth corners). When it finds none, it tries once more on the image with a border added, which catches faces that fill the frame.
3. **Recognition**: each face is aligned on its landmarks and embedded with ArcFace.
4. **Scoring**: a file's score is its best face's cosine similarity to the reference face. A video takes the best across its sampled frames.
5. **Copy or move**: files at or above the threshold are copied or moved when you click **Round up**.

## Performance

Both models run on the Neural Engine and GPU through Core ML. Files are read and decoded on four threads and the models run on two, ahead of the file being reported: reading, decoding and recognition overlap.

On an M1 Max, a scan of 12-megapixel JPEGs runs at about 20 photos a second. HEIC photos take longer to decode.

Folder listing reads each folder once, with no per-file lookups.

## The face model

Vaquero uses `buffalo_l`, a face model pack from [InsightFace](https://github.com/deepinsight/insightface), the open-source face analysis project.

| Pack | Files | Size | Recognizer |
|---|---|---|---|
| `buffalo_l` | `det_10g.onnx`, `w600k_r50.onnx` | 183 MB | ArcFace R50, WebFace600K |

On first launch, the app downloads `buffalo_l.zip`, unmodified, from Vaquero's own [model release](https://github.com/BitWise-0x/Vaquero/releases/tag/model-buffalo_l-1), falling back to InsightFace's [model-zoo](https://github.com/deepinsight/insightface/releases/tag/model-zoo) and [v0.7](https://github.com/deepinsight/insightface/releases/tag/v0.7) releases of the same file. The archive is checked against a pinned size and SHA-256, and the detector and recognizer against their own pinned SHA-256, before they are unpacked into `~/Library/Application Support/Vaquero/models/buffalo_l/`. Each launch checks the installed files against their pinned sizes and downloads a damaged pack again.

A failed download is tried again up to four times, waiting longer each time, and resumes where it stopped. Model and update downloads also resume after a quit or a cancel, and need no GitHub account.

A pack needs a detector (`det_*` or `scrfd*`) and a recognizer (`w600k*` or `glintr*`); other files are ignored.

The InsightFace pretrained models are licensed for non-commercial research use only. They are not part of Vaquero and not covered by its license.
