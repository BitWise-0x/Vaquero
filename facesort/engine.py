"""Face detection (SCRFD) and recognition (ArcFace) on insightface model packs (buffalo_l, antelopev2, ...)."""

from __future__ import annotations

import csv
import logging
import shutil
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from itertools import islice
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

log = logging.getLogger("facesort")
# CoreML partition warnings on every session are noise for users
ort.set_default_logger_severity(3)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
DET_PREFIXES = ("det_", "scrfd")
REC_PREFIXES = ("w600k", "glintr")
DEFAULT_VIDEO_FRAMES = 10
PREFETCH_WORKERS = 4
PREFETCH_DEPTH = 8  # decoded files held in memory ahead of the model
# a reference whose largest face sits this far from the group is someone else
REF_OUTLIER_SIM = 0.4
# a face "agrees" with a single reference photo at this similarity; lookalikes match the centroid but few photos
AGREE_SIM = 0.35
# border added on each side, as a fraction of the longer edge, when the first detection pass finds nothing
PAD_RETRY = 0.25

# ArcFace canonical 5-point landmark positions in a 112x112 crop
ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)


@dataclass(frozen=True)
class Face:
    bbox: np.ndarray  # x1, y1, x2, y2
    kps: np.ndarray  # 5x2 landmarks
    score: float

    @property
    def area(self) -> float:
        return float((self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1]))


@dataclass(frozen=True)
class ScanResult:
    path: Path
    kind: str  # "image" | "video"
    score: float  # best face similarity to the reference centroid, 0 when no face
    faces: int
    agree: int = 0  # reference photos the best face agrees with (similarity >= AGREE_SIM)


def _session(path: Path) -> ort.InferenceSession:
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {path}")
    providers = [p for p in ("CoreMLExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    return ort.InferenceSession(str(path), providers=providers)


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class Detector:
    """SCRFD 10G: 3 strides, 2 anchors per cell, outputs ordered scores/bbox/kps."""

    STRIDES = (8, 16, 32)
    INPUT = 640

    def __init__(self, model: Path, score_thresh: float = 0.5, nms_thresh: float = 0.4) -> None:
        self.session = _session(model)
        self.input_name = self.session.get_inputs()[0].name
        self.score_thresh = score_thresh
        self.nms_thresh = nms_thresh
        self._centers: dict[int, np.ndarray] = {}

    def _anchor_centers(self, stride: int) -> np.ndarray:
        if stride not in self._centers:
            n = self.INPUT // stride
            grid = np.stack(np.mgrid[:n, :n][::-1], axis=-1).astype(np.float32) * stride
            self._centers[stride] = np.repeat(grid.reshape(-1, 2), 2, axis=0)
        return self._centers[stride]

    def detect(self, img: np.ndarray) -> list[Face]:
        h, w = img.shape[:2]
        scale = self.INPUT / max(h, w)
        resized = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        canvas = np.zeros((self.INPUT, self.INPUT, 3), dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (self.INPUT, self.INPUT), (127.5, 127.5, 127.5), swapRB=True)
        outs = [o[0] if o.ndim == 3 else o for o in self.session.run(None, {self.input_name: blob})]

        boxes, kpss, scores = [], [], []
        n = len(self.STRIDES)
        for i, stride in enumerate(self.STRIDES):
            s = outs[i].reshape(-1)
            keep = np.where(s >= self.score_thresh)[0]
            if not keep.size:
                continue
            c = self._anchor_centers(stride)[keep]
            d = outs[i + n][keep] * stride
            k = outs[i + 2 * n][keep].reshape(-1, 5, 2) * stride
            boxes.append(np.hstack([c - d[:, :2], c + d[:, 2:]]))
            kpss.append(k + c[:, None, :])
            scores.append(s[keep])
        if not boxes:
            return []
        b = np.vstack(boxes) / scale
        k = np.vstack(kpss) / scale
        sc = np.concatenate(scores)
        keep = _nms(np.hstack([b, sc[:, None]]), self.nms_thresh)
        return [Face(bbox=b[i], kps=k[i], score=float(sc[i])) for i in keep]


class Recognizer:
    SIZE = 112

    def __init__(self, model: Path) -> None:
        self.session = _session(model)
        self.input_name = self.session.get_inputs()[0].name

    def embed(self, img: np.ndarray, face: Face) -> np.ndarray:
        m, _ = cv2.estimateAffinePartial2D(face.kps.astype(np.float32), ARCFACE_DST, method=cv2.LMEDS)
        if m is None:
            raise ValueError("landmark alignment failed")
        crop = cv2.warpAffine(img, m, (self.SIZE, self.SIZE), borderValue=0.0)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (self.SIZE, self.SIZE), (127.5, 127.5, 127.5), swapRB=True)
        v = self.session.run(None, {self.input_name: blob})[0][0]
        return v / np.linalg.norm(v)


def pack_models(pack: Path) -> tuple[Path, Path] | None:
    """Detector and recognizer ONNX files in an insightface model pack folder, None if either is missing."""
    files = sorted(pack.glob("*.onnx"))
    det = next((f for f in files if f.name.startswith(DET_PREFIXES)), None)
    rec = next((f for f in files if f.name.startswith(REC_PREFIXES)), None)
    return (det, rec) if det and rec else None


class FaceMatcher:
    def __init__(self, model_dir: Path) -> None:
        models = pack_models(model_dir)
        if models is None:
            raise FileNotFoundError(f"no detector + recognizer pair in {model_dir}")
        self.detector = Detector(models[0])
        self.recognizer = Recognizer(models[1])
        self.centroid: np.ndarray | None = None
        self.refs: np.ndarray | None = None  # kept reference embeddings, one row per photo

    def embeddings(self, img: np.ndarray) -> list[tuple[Face, np.ndarray]]:
        faces = self.detector.detect(img)
        if not faces:
            # SCRFD misses a face that fills the frame (tight crops); a border shrinks it into range
            b = int(max(img.shape[:2]) * PAD_RETRY)
            img = cv2.copyMakeBorder(img, b, b, b, b, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            faces = self.detector.detect(img)
        return [(f, self.recognizer.embed(img, f)) for f in faces]

    def load_references(self, ref_dir: Path) -> int:
        """Build the reference centroid from the largest face in each image; returns refs kept."""
        refs: list[tuple[str, np.ndarray]] = []
        for p in sorted(ref_dir.iterdir()):
            if p.suffix.lower() not in IMAGE_EXTS:
                continue
            img = read_image(p)
            if img is None:
                continue
            found = self.embeddings(img)
            if not found:
                log.warning("reference has no face: %s", p.name)
                continue
            refs.append((p.name, max(found, key=lambda fe: fe[0].area)[1]))
        if not refs:
            raise ValueError(f"no faces found in reference folder {ref_dir}")

        centroid = _unit(np.stack([e for _, e in refs]).mean(0))
        kept = []
        for name, e in refs:
            sim = float(e @ centroid)
            if sim < REF_OUTLIER_SIM:
                log.warning("reference dropped, largest face is a different person (sim %.3f): %s", sim, name)
            else:
                kept.append(e)
        if not kept:
            raise ValueError("references disagree with each other, no consistent face")
        self.refs = np.stack(kept)
        self.centroid = _unit(self.refs.mean(0))
        log.info("references: %d kept of %d with a face", len(kept), len(refs))
        return len(kept)

    @property
    def ref_count(self) -> int:
        return 0 if self.refs is None else len(self.refs)

    def best_score(self, frames: Iterator[np.ndarray]) -> tuple[float, int, int]:
        """(best centroid similarity, faces seen, reference photos the best face agrees with)."""
        if self.centroid is None or self.refs is None:
            raise RuntimeError("load_references must run before scoring")
        best, count, agree = 0.0, 0, 0
        for img in frames:
            for _, e in self.embeddings(img):
                count += 1
                score = float(e @ self.centroid)
                if score > best:
                    best, agree = score, int((self.refs @ e >= AGREE_SIM).sum())
        return best, count, agree


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def read_image(path: Path) -> np.ndarray | None:
    # imdecode handles non-ASCII paths that imread can trip on
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        log.warning("unreadable image: %s", path)
    return img


def video_frames(path: Path, samples: int = DEFAULT_VIDEO_FRAMES) -> Iterator[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or total <= 0:
            log.warning("unreadable video: %s", path)
            return
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, frame = cap.read()
            if ok:
                yield frame
    finally:
        cap.release()


def list_media(folder: Path, include_videos: bool, recursive: bool = False, exclude: Path | None = None) -> list[Path]:
    """Media files in folder; recursive skips hidden folders and exclude (the move destination)."""
    exts = IMAGE_EXTS | VIDEO_EXTS if include_videos else IMAGE_EXTS
    skip = exclude.resolve() if exclude else None
    found = []
    for p in folder.rglob("*") if recursive else folder.iterdir():
        rel = p.relative_to(folder).parts
        if any(part.startswith(".") for part in rel) or not p.is_file() or p.suffix.lower() not in exts:
            continue
        if skip and p.resolve().is_relative_to(skip):
            continue
        found.append(p)
    return sorted(found)


def scan(
    matcher: FaceMatcher,
    folder: Path,
    include_videos: bool,
    cancelled: Callable[[], bool] = lambda: False,
    recursive: bool = False,
    exclude: Path | None = None,
    video_samples: int = DEFAULT_VIDEO_FRAMES,
) -> Iterator[ScanResult]:
    """Score every media file in folder, entering subfolders only when recursive."""
    paths = iter(list_media(folder, include_videos, recursive, exclude))
    load = partial(_load_frames, video_samples=video_samples)
    # decoding outweighs Core ML inference, so files decode on a bounded thread pool ahead of the model
    with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
        pending: deque[tuple[Path, Future[list[np.ndarray]]]] = deque()
        for path in islice(paths, PREFETCH_DEPTH):
            pending.append((path, pool.submit(load, path)))
        while pending:
            path, fut = pending.popleft()
            if cancelled():
                for _, f in pending:
                    f.cancel()
                return
            nxt = next(paths, None)
            if nxt is not None:
                pending.append((nxt, pool.submit(load, nxt)))
            kind = "video" if path.suffix.lower() in VIDEO_EXTS else "image"
            try:
                score, n, agree = matcher.best_score(iter(fut.result()))
            except (cv2.error, ValueError, OSError) as e:
                log.error("failed on %s: %s", path.name, e)
                continue
            yield ScanResult(path=path, kind=kind, score=score, faces=n, agree=agree)


def _load_frames(path: Path, video_samples: int) -> list[np.ndarray]:
    if path.suffix.lower() in VIDEO_EXTS:
        return list(video_frames(path, video_samples))
    img = read_image(path)
    return [img] if img is not None else []


class Conflict(StrEnum):
    """What to do when the destination already has a file with the same name."""

    KEEP_BOTH = "keep_both"
    OVERWRITE = "overwrite"


def _free_name(dst: Path) -> Path:
    """Finder-style next free name: 'IMG_1.png' -> 'IMG_1 2.png', 'IMG_1 3.png', ..."""
    n = 2
    while (candidate := dst.with_name(f"{dst.stem} {n}{dst.suffix}")).exists():
        n += 1
    return candidate


class Transfer(StrEnum):
    MOVE = "move"
    COPY = "copy"


def transfer_matches(
    paths: list[Path],
    dest: Path,
    journal: Path,
    progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    conflict: Conflict = Conflict.KEEP_BOTH,
    mode: Transfer = Transfer.MOVE,
) -> tuple[int, int]:
    """Move or copy files into dest; returns (done, failed).

    The journal records src,dst,action per file for undo: action is moved or copied, plus ", renamed" when the
    name was taken and both were kept, or ", overwrote" when the existing file was replaced.
    Cancelling stops between files, so no file is ever left half moved or copied."""
    dest.mkdir(parents=True, exist_ok=True)
    verb = "moved" if mode is Transfer.MOVE else "copied"
    done = failed = 0
    with journal.open("a", newline="") as fh:
        w = csv.writer(fh)
        for i, src in enumerate(paths, 1):
            if cancelled():
                log.warning("%s cancelled after %d of %d files", mode.value, i - 1, len(paths))
                return done, failed
            progress(i - 1, len(paths))
            dst, action = dest / src.name, verb
            if dst.exists():
                if conflict is Conflict.OVERWRITE:
                    action = f"{verb}, overwrote"
                    log.warning("overwriting existing %s", dst, extra={"link": dst, "link_text": str(dst)})
                else:
                    dst, action = _free_name(dst), f"{verb}, renamed"
            try:
                if mode is Transfer.MOVE:
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(src, dst)
            except OSError as e:
                log.error("%s failed %s -> %s: %s", mode.value, src, dst, e)
                failed += 1
                continue
            w.writerow([str(src), str(dst), action])
            done += 1
            link = {"link": dst, "link_text": dst.name}
            if action.endswith("renamed"):
                log.info("%s %s, name taken, kept both as %s", verb, src.name, dst.name, extra=link)
            else:
                log.info("%s %s", verb, dst.name, extra=link)
    progress(len(paths), len(paths))
    return done, failed
