---
title: Files
description: The logs, scan reports and move journal Vaquero writes, and where they live.
---

## Logs and scan reports

In `~/Library/Logs/Vaquero`. Open it with **Show logs folder** in the help window, or from **Vaquero > Settings…**.

| File | Contents |
|---|---|
| `vaquero-<timestamp>.log` | The log of one scan and the copy or move that follows it; each scan starts a new one |
| `vaquero-scan-<timestamp>.csv` | Every scanned file with its score, face count and reference agreement (`ref_agree`, `N/M`) |

The log files are plain text, with `== Section ==` lines between sections.

## Move journal

In `~/Library/Application Support/Vaquero`:

| File | Contents |
|---|---|
| `vaquero-moves.csv` | One line per copied or moved file: `source,destination,action` |

The action is `moved` or `copied`, plus `, renamed` when the name was taken and both were kept, or `, overwrote` when the existing file was replaced. Every copy and move can be undone from it; an overwritten file is gone.

## Face model

The face model lives in `~/Library/Application Support/Vaquero/models/`. A partial download stays in `~/Library/Application Support/Vaquero/downloads/` and resumes on the next attempt. See [How it works](how-it-works.md#the-face-model).
