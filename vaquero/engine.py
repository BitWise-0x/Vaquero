"""Face detection (SCRFD) and recognition (ArcFace) on insightface model packs (buffalo_l, antelopev2, ...)."""

from __future__ import annotations

import csv
import errno
import fcntl
import logging
import os
import shutil
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from itertools import islice
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import onnxruntime as ort
import pi_heif

log = logging.getLogger("vaquero")
# CoreML partition warnings on every session are noise for users
ort.set_default_logger_severity(3)

HEIF_EXTS = {".heic", ".heif"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} | HEIF_EXTS
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
DET_PREFIXES = ("det_", "scrfd")
REC_PREFIXES = ("w600k", "glintr")
DEFAULT_VIDEO_FRAMES = 10
DECODE_WORKERS = 4
# two model runs in flight keep Core ML busy while the other thread aligns and scores
MODEL_WORKERS = 2
PREFETCH_DEPTH = 8  # files decoded or scored ahead of the one being reported
# a reference photo whose chosen face sits this far from the average of the other references is someone else
REF_OUTLIER_SIM = 0.25
# two people this close in how often they appear across the references leave the choice in doubt
REF_AMBIGUOUS = 0.9
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


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity (Umeyama) mapping src points onto dst, as a 2x3 matrix: InsightFace's alignment."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    u, sigma, vt = np.linalg.svd(d.T @ s / len(src))
    flip = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        flip[1, 1] = -1
    rot = u @ flip @ vt
    scale = np.trace(np.diag(sigma) @ flip) / s.var(0).sum()
    return np.hstack([scale * rot, (mu_d - scale * rot @ mu_s)[:, None]]).astype(np.float32)


class Recognizer:
    SIZE = 112

    def __init__(self, model: Path) -> None:
        self.session = _session(model)
        self.input_name = self.session.get_inputs()[0].name

    def embed_many(self, img: np.ndarray, faces: list[Face]) -> np.ndarray:
        """Unit embeddings of faces in img, one row per face.

        One face per run: Core ML recompiles the model whenever the batch size changes, which costs far more
        than the runs it would save."""
        rows = []
        for f in faces:
            m = similarity_transform(f.kps.astype(np.float64), ARCFACE_DST)
            crop = cv2.warpAffine(img, m, (self.SIZE, self.SIZE))
            blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (self.SIZE, self.SIZE), (127.5, 127.5, 127.5), swapRB=True)
            rows.append(self.session.run(None, {self.input_name: blob})[0][0])
        v = np.stack(rows)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


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
        if not faces:
            return []
        return list(zip(faces, self.recognizer.embed_many(img, faces), strict=True))

    def load_references(self, ref_dir: Path) -> int:
        """Build the reference centroid from the person common to the reference photos; returns photos kept.

        A photo may show other people too: in each, the face of the person who appears across the photos is used.
        A photo whose face differs from the average of the other photos is someone else and is dropped."""
        photos: list[tuple[str, np.ndarray]] = []  # name, that photo's embeddings with the largest face first
        ref_paths: dict[str, Path] = {}  # name to file, so the log can link each photo it mentions
        for p in sorted(ref_dir.iterdir()):
            if p.suffix.lower() not in IMAGE_EXTS:
                continue
            img = read_image(p)
            if img is None:
                continue
            found = sorted(self.embeddings(img), key=lambda fe: -fe[0].area)
            if not found:
                log.warning("reference has no face: %s", p.name, extra={"link": p, "link_text": p.name})
                continue
            photos.append((p.name, np.stack([e for _, e in found])))
            ref_paths[p.name] = p
        if not photos:
            raise ValueError(f"no faces found in reference folder {ref_dir}")

        person = _common_person(photos)
        chosen = []
        for name, faces in photos:
            i = int(np.argmax(faces @ person))
            if i:
                log.info(
                    "reference %s: %d faces, using the one that matches the other photos", name, len(faces),
                    extra={"link": ref_paths[name], "link_text": name},
                )
            chosen.append((name, faces[i]))
        kept = []
        for i, (name, e) in enumerate(chosen):
            others = [o for j, (_, o) in enumerate(chosen) if j != i]
            sim = float(e @ _unit(np.mean(others, 0))) if others else 1.0
            if sim < REF_OUTLIER_SIM:
                log.warning(
                    "reference dropped, its face is a different person from the others (sim %.3f): %s", sim, name,
                    extra={"link": ref_paths[name], "link_text": name},
                )
            else:
                kept.append(e)
        if not kept:
            raise ValueError("references disagree with each other, no consistent face")
        self.refs = np.stack(kept)
        self.centroid = _unit(self.refs.mean(0))
        log.info("references: %d kept of %d with a face", len(kept), len(photos))
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


def _common_person(photos: list[tuple[str, np.ndarray]]) -> np.ndarray:
    """The embedding of the face whose best matches in the other photos are strongest: the person they share.

    Ties, as with a single photo, go to the larger face. Logs a warning when a second person is about as common."""
    support: list[tuple[float, np.ndarray]] = []
    for i, (_, faces) in enumerate(photos):
        for e in faces:
            s = sum(float((other @ e).max()) for j, (_, other) in enumerate(photos) if j != i)
            support.append((s, e))
    order = sorted(range(len(support)), key=lambda k: -support[k][0])  # stable: larger faces first on ties
    best, e = support[order[0]]
    rival = next((support[k] for k in order[1:] if float(support[k][1] @ e) < AGREE_SIM), None)
    if rival and best > 0 and rival[0] >= REF_AMBIGUOUS * best and rival[0] / max(len(photos) - 1, 1) >= AGREE_SIM:
        log.warning("the reference photos show two people about equally often; add a photo of the person alone")
    return e


def read_image(path: Path) -> np.ndarray | None:
    """The image as BGR pixels, upright per its orientation, or None when it cannot be decoded."""
    if path.suffix.lower() in HEIF_EXTS:
        img = _read_heif(path)
    else:
        # imdecode handles non-ASCII paths that imread can trip on
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        log.warning("unreadable image: %s", path, extra={"link": path, "link_text": str(path)})
    return img


def _read_heif(path: Path) -> np.ndarray | None:
    try:
        # pixels decode on first access, so a damaged file can fail at asarray as well as at open
        px = np.asarray(pi_heif.open_heif(str(path), convert_hdr_to_8bit=True))
    except (ValueError, RuntimeError) as e:  # pi-heif raises these for corrupt or unsupported files
        log.debug("HEIF decode failed for %s: %s", path, e)
        return None
    if px.ndim == 2:
        return cv2.cvtColor(px, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(px, cv2.COLOR_RGBA2BGR if px.shape[2] == 4 else cv2.COLOR_RGB2BGR)


def video_frames(path: Path, samples: int = DEFAULT_VIDEO_FRAMES) -> Iterator[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or total <= 0:
            log.warning("unreadable video: %s", path, extra={"link": path, "link_text": str(path)})
            return
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, frame = cap.read()
            if ok:
                yield frame
    finally:
        cap.release()


def list_media(folder: Path, include_videos: bool, recursive: bool = False, exclude: Path | None = None) -> list[Path]:
    """Media files in folder, sorted; recursive skips hidden folders and exclude (the move destination).

    One directory read per folder, with no per-file lookups, keeps network shares fast. An unreadable subfolder is
    logged and skipped; an unreadable folder itself raises."""
    exts = IMAGE_EXTS | VIDEO_EXTS if include_videos else IMAGE_EXTS
    skip = os.path.realpath(exclude) if exclude else None
    found: list[Path] = []
    stack = [os.fspath(folder)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as e:
            if current == os.fspath(folder):
                raise
            log.warning(
                "skipped unreadable folder %s: %s", current, e.strerror or e, extra={"link": Path(current), "link_text": current}
            )
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                if recursive and os.path.realpath(entry.path) != skip:
                    stack.append(entry.path)
            elif os.path.splitext(entry.name)[1].lower() in exts and entry.is_file():
                found.append(Path(entry.path))
    return sorted(found)


def scan(
    matcher: FaceMatcher,
    paths: list[Path],
    cancelled: Callable[[], bool] = lambda: False,
    video_samples: int = DEFAULT_VIDEO_FRAMES,
) -> Iterator[ScanResult]:
    """Score each file, in order. Files decode on one thread pool and go through the model on another, both
    working ahead of the file being reported, so reading, decoding and the model overlap."""
    queue = iter(paths)
    load = partial(_load_frames, video_samples=video_samples)

    def score(frames: Future[list[np.ndarray]]) -> tuple[float, int, int]:
        return matcher.best_score(iter(frames.result()))

    with ThreadPoolExecutor(DECODE_WORKERS) as decode, ThreadPoolExecutor(MODEL_WORKERS) as model:

        def submit(path: Path) -> None:
            pending.append((path, model.submit(score, decode.submit(load, path))))

        pending: deque[tuple[Path, Future[tuple[float, int, int]]]] = deque()
        for path in islice(queue, PREFETCH_DEPTH):
            submit(path)
        while pending:
            path, fut = pending.popleft()
            if cancelled():
                # drop queued work; the pools finish only what is already running
                decode.shutdown(wait=False, cancel_futures=True)
                model.shutdown(wait=False, cancel_futures=True)
                return
            nxt = next(queue, None)
            if nxt is not None:
                submit(nxt)
            kind = "video" if path.suffix.lower() in VIDEO_EXTS else "image"
            try:
                best, n, agree = fut.result()
            except (cv2.error, ValueError, OSError) as e:
                log.error("failed on %s: %s", path.name, e, extra={"link": path, "link_text": path.name})
                continue
            yield ScanResult(path=path, kind=kind, score=best, faces=n, agree=agree)


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


# errors that every later file in the batch would hit too: stop instead of failing them one by one
FATAL_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.ENXIO, errno.ENODEV})


class TransferCounts(NamedTuple):
    done: int
    failed: int
    skipped: int = 0  # already in the destination, as from a run that stopped part way


class TransferStopped(RuntimeError):
    """The destination stopped taking files (disk full, read-only, unmounted); the batch ended early."""

    def __init__(self, reason: str, counts: TransferCounts) -> None:
        super().__init__(reason)
        self.counts = counts


# FAT and exFAT keep modification times to 2 seconds
MTIME_SLACK_S = 2


def _already_there(src: Path, dst: Path) -> bool:
    """rsync's quick check: same size and modification time. A copy keeps the original's time, so a file copied
    by an earlier run matches, and a different photo that happens to share the name does not."""
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) <= MTIME_SLACK_S


def _sync(fh: object) -> None:
    """Flush a file to the disk itself: on macOS fsync only reaches the drive's cache, F_FULLFSYNC goes further.
    Network and some external file systems refuse F_FULLFSYNC, so fall back to fsync there."""
    fd = fh.fileno()  # type: ignore[attr-defined]
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (AttributeError, OSError):
        os.fsync(fd)


def _place(src: Path, dst: Path, mode: Transfer) -> bool:
    """Put src at dst, replacing what is there; True when a Move kept its source.

    A Move within one volume is a single atomic rename. Anything else is copied to a hidden name beside dst, flushed
    to disk and size-checked, then renamed over dst, so a failed copy never leaves a partial file under the real name
    or touches the file it would replace. A Move removes its source only after that."""
    if mode is Transfer.MOVE and src.stat().st_dev == dst.parent.stat().st_dev:
        os.replace(src, dst)
        return False
    tmp = dst.with_name(f".{dst.name}.vaquero-part")
    try:
        shutil.copy2(src, tmp)
        with tmp.open("rb+") as fh:
            _sync(fh)
        if (got := tmp.stat().st_size) != (want := src.stat().st_size):
            raise OSError(errno.EIO, f"copy is {got} bytes, the original {want}", str(dst))
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if mode is Transfer.COPY:
        return False
    try:
        src.unlink()
    except OSError as e:
        log.error("copied %s but could not remove the original: %s", src, e, extra={"link": src, "link_text": str(src)})
        return True
    return False


def transfer_matches(
    paths: list[Path],
    dest: Path,
    journal: Path,
    progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    conflict: Conflict = Conflict.KEEP_BOTH,
    mode: Transfer = Transfer.MOVE,
) -> TransferCounts:
    """Move or copy files into dest.

    The journal records src,dst,action per file for undo: action is moved or copied, plus ", renamed" when the
    name was taken and both were kept, or ", overwrote" when the existing file was replaced. Each row is on disk
    before the next file starts. Overwrite replaces only files that were there before this batch: two matches with
    the same name from different subfolders are both kept. Cancelling stops between files, so no file is ever left
    half moved or copied. A file already in the destination (see _already_there) is skipped, so running a stopped
    batch again does not duplicate what it did. Raises TransferStopped when the destination stops taking files."""
    dest.mkdir(parents=True, exist_ok=True)
    for stale in dest.glob(".*.vaquero-part"):  # left by a copy the app was quit or killed in the middle of
        stale.unlink(missing_ok=True)
    verb = "moved" if mode is Transfer.MOVE else "copied"
    done = failed = skipped = 0
    placed: set[str] = set()  # names written by this batch, casefolded as APFS and HFS+ compare them
    with journal.open("a", newline="") as fh:
        w = csv.writer(fh)
        for i, src in enumerate(paths, 1):
            if cancelled():
                log.warning("%s cancelled after %d of %d files", mode.value, i - 1, len(paths))
                return TransferCounts(done, failed, skipped)
            progress(i - 1, len(paths))
            dst, action = dest / src.name, verb
            if dst.name.casefold() not in placed and _already_there(src, dst):
                log.info("%s is already in the destination, skipped", src.name, extra={"link": dst, "link_text": dst.name})
                skipped += 1
                continue
            if dst.exists():
                if conflict is Conflict.OVERWRITE and dst.name.casefold() not in placed:
                    action = f"{verb}, overwrote"
                    log.warning("overwriting existing %s", dst, extra={"link": dst, "link_text": str(dst)})
                else:
                    dst, action = _free_name(dst), f"{verb}, renamed"
            try:
                if _place(src, dst, mode):
                    action = action.replace("moved", "copied", 1)
            except OSError as e:
                log.error("%s failed %s -> %s: %s", mode.value, src, dst, e, extra={"link": src, "link_text": str(src)})
                failed += 1
                if e.errno in FATAL_ERRNOS or not dest.is_dir():
                    reason = (
                        f"{dest} is no longer available"
                        if not dest.is_dir()
                        else f"{dest} cannot take more files: {os.strerror(e.errno or 0)}"
                    )
                    log.error("%s stopped after %d of %d files: %s", mode.value, i, len(paths), reason)
                    raise TransferStopped(reason, TransferCounts(done, failed, skipped)) from e
                continue
            placed.add(dst.name.casefold())
            try:
                w.writerow([str(src), str(dst), action])
                fh.flush()
                _sync(fh)
            except OSError as e:
                raise OSError(e.errno, f"{dst.name} was {verb} but the undo list {journal} could not be written: {e}") from e
            done += 1
            link = {"link": dst, "link_text": dst.name}
            if action.endswith("renamed"):
                log.info("%s %s, name taken, kept both as %s", verb, src.name, dst.name, extra=link)
            else:
                log.info("%s %s", verb, dst.name, extra=link)
    progress(len(paths), len(paths))
    return TransferCounts(done, failed, skipped)
