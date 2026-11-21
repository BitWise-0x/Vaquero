---
title: Troubleshooting
description: Fixes for the common problems.
---

| Problem | Fix |
|---|---|
| The face model download fails on first launch | Check the internet connection, then reopen Vaquero. The download resumes where it stopped. |
| A reference photo was dropped | It shows someone else. Remove it or replace it. See [Reference photos](reference-photos.md#messages). |
| `reference has no face` | No face was found, usually a profile, a covered face or a very small one. Replace it with a clearer photo. |
| `the reference photos show two people about equally often` | Add a photo of the person alone. |
| Expected matches are missing | Open the scan report, sort by score, and lower the threshold to just below the missed files. See [Scores and the threshold](using.md#scores-and-the-threshold). |
| A folder on a drive or share cannot be read | Vaquero shows a window with **Open Settings**. Allow Vaquero in **System Settings > Privacy & Security > Files and Folders**. |
| A file cannot be read | It is logged and skipped; the scan continues. |
| `Stopped: ... cannot take more files` | The destination is full or read-only. Free space or pick another destination, then click **Execute** again; files already done are not repeated. |
| `Stopped: ... is no longer available` | The destination drive or share was disconnected. Reconnect it and click **Execute** again. |
| "Still finishing the current file" on close | A long video is mid-read. Close again a moment later. |
