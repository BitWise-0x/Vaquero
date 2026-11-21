---
title: Reference photos
description: The photos Vaquero learns a face from, and what makes a good set.
---

The **Reference faces** folder holds a few photos of the person to find. Vaquero learns their face from these photos, with no training step.

## A good set

- **Three to five photos.** More is fine; one or two work, with less protection against a wrong photo.
- **Clear, well-lit faces** from different angles and, ideally, different years.
- **Other people are fine.** Vaquero uses the person who appears across the photos, even when someone else's face is bigger.
- **At least one photo of the person alone** when every other photo shows the same pair, such as a couple. Without it, the two are equally likely, and the log warns.

Any [photo type](using.md#file-types) works, including HEIC.

## How the person is chosen

1. Every face in every reference photo is found.
2. The person is the one whose face appears across the most photos. A single photo with several faces uses the largest.
3. In each photo, the face closest to that person is used.
4. Each photo is then compared with the average of the others. A photo of someone else falls far from it and is dropped, with a warning in the log.

The log's **References** section lists each reference photo kept, with how closely its face matches the others, how many were kept, and names any photo that was dropped, had no face, or used a face other than the largest.

## Messages

| Log message | Meaning |
|---|---|
| `reference has no face` | No face was found in that photo, usually a profile, a covered face or a very small one. Replace it with a clearer photo. |
| `reference dropped, its face is a different person from the others` | That photo shows someone else. Remove it or replace it. |
| `the reference photos show two people about equally often` | Two people appear together in most photos. Add a photo of the person alone. |
| `references disagree with each other` | No two photos show the same person. Check the folder. |
