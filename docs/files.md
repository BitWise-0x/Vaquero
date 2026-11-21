---
title: Files
description: The logs, scan reports and move journal Vaquero writes, and where they live.
---

## Logs and scan reports

In `~/Library/Logs/Vaquero`. Open it with **Show logs folder** in the help window, or from **Vaquero > Settings…**.

| File | Contents |
|---|---|
| `vaquero.log` | Everything from launch on: the model download, update checks, every scan, copy and move, and the full details of any error. Rotates at 2 MB, keeping three older files (`vaquero.log.1` to `.3`) |
| `vaquero-update.log` | Each update install: the swap of the old app for the new one, or why it could not be made |
| `vaquero-crash.log` | A crash inside the face model, OpenCV or Qt, with the stack of every thread |
| `vaquero-<timestamp>.log` | The log of one scan and the copy or move that follows it; each scan starts a new one |
| `vaquero-scan-<timestamp>.csv` | Every scanned file with its score, face count and reference agreement (`ref_agree`, `N/M`) |

The log files are plain text, with `== Section ==` lines between sections. The newest 50 scan logs and scan reports are kept; older ones are removed when Vaquero opens.

## Move journal

In `~/Library/Application Support/Vaquero`:

| File | Contents |
|---|---|
| `vaquero-moves.csv` | One line per copied or moved file: `source,destination,action,run,trashed` |

The action is `moved` or `copied`, plus `, renamed` when the name was taken and both were kept, or `, overwrote` when the existing file was replaced. `run` groups the files of one copy or move, and `trashed` is where an overwritten file went in the Trash (empty on a volume without a Trash, such as some network shares, where it was replaced outright). **Edit > Undo** reverses the newest run and adds an `undone` line for it. Lines from older versions have only the first three columns.

## Face model

The face model lives in `~/Library/Application Support/Vaquero/models/`. A partial download stays in `~/Library/Application Support/Vaquero/downloads/` and resumes on the next attempt. See [How it works](how-it-works.md#the-face-model).
