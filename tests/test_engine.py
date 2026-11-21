import csv
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vaquero.engine import (
    Conflict,
    Transfer,
    list_media,
    pack_models,
    transfer_matches,
)


def touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_list_media_is_top_level_only(tmp_path: Path) -> None:
    touch(tmp_path / "a.JPG")
    touch(tmp_path / "b.mp4")
    touch(tmp_path / "notes.txt")
    touch(tmp_path / ".hidden.jpg")
    touch(tmp_path / "sub" / "c.jpg")
    assert [p.name for p in list_media(tmp_path, include_videos=True)] == ["a.JPG", "b.mp4"]
    assert [p.name for p in list_media(tmp_path, include_videos=False)] == ["a.JPG"]


def test_list_media_recursive_skips_hidden_and_destination(tmp_path: Path) -> None:
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "sub" / "b.jpg")
    touch(tmp_path / ".cache" / "c.jpg")
    touch(tmp_path / "dest" / "d.jpg")
    found = list_media(tmp_path, include_videos=False, recursive=True, exclude=tmp_path / "dest")
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["a.jpg", "sub/b.jpg"]


def test_pack_models_needs_detector_and_recognizer(tmp_path: Path) -> None:
    touch(tmp_path / "l" / "det_10g.onnx")
    touch(tmp_path / "l" / "w600k_r50.onnx")
    touch(tmp_path / "l" / "genderage.onnx")
    touch(tmp_path / "a" / "scrfd_10g_bnkps.onnx")
    touch(tmp_path / "a" / "glintr100.onnx")
    touch(tmp_path / "half" / "det_10g.onnx")
    assert pack_models(tmp_path / "l") == (tmp_path / "l" / "det_10g.onnx", tmp_path / "l" / "w600k_r50.onnx")
    assert pack_models(tmp_path / "a") == (tmp_path / "a" / "scrfd_10g_bnkps.onnx", tmp_path / "a" / "glintr100.onnx")
    assert pack_models(tmp_path / "half") is None


def test_move_keep_both_renames_and_journals(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    a = touch(src / "a.jpg", b"newer")
    b = touch(src / "b.jpg")
    touch(dest / "a.jpg", b"old")
    touch(dest / "a 2.jpg", b"older")
    journal = tmp_path / "moves.csv"

    ticks: list[tuple[int, int]] = []
    moved, failed, _ = transfer_matches([a, b], dest, journal, lambda d, t: ticks.append((d, t)))

    assert (moved, failed, _) == (2, 0, 0)
    assert ticks[-1] == (2, 2)
    assert (dest / "a.jpg").read_bytes() == b"old"
    assert (dest / "a 2.jpg").read_bytes() == b"older"
    assert (dest / "a 3.jpg").read_bytes() == b"newer"
    assert not a.exists() and not b.exists()
    assert list(csv.reader(journal.open())) == [
        [str(a), str(dest / "a 3.jpg"), "moved, renamed"],
        [str(b), str(dest / "b.jpg"), "moved"],
    ]


def test_move_overwrite_replaces(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    a = touch(src / "a.jpg", b"newer")
    touch(dest / "a.jpg", b"old")
    journal = tmp_path / "moves.csv"

    assert transfer_matches([a], dest, journal, conflict=Conflict.OVERWRITE) == (1, 0, 0)
    assert (dest / "a.jpg").read_bytes() == b"newer"
    assert sorted(p.name for p in dest.iterdir()) == ["a.jpg"]
    assert list(csv.reader(journal.open())) == [[str(a), str(dest / "a.jpg"), "moved, overwrote"]]



def test_copy_keeps_the_source(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    a = touch(src / "a.jpg", b"newer")
    touch(dest / "a.jpg", b"old")
    journal = tmp_path / "moves.csv"

    assert transfer_matches([a], dest, journal, mode=Transfer.COPY) == (1, 0, 0)
    assert a.read_bytes() == b"newer"
    assert (dest / "a.jpg").read_bytes() == b"old"
    assert (dest / "a 2.jpg").read_bytes() == b"newer"
    assert list(csv.reader(journal.open())) == [[str(a), str(dest / "a 2.jpg"), "copied, renamed"]]


MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "buffalo_l"
TIGHT_CROP = os.environ.get("VAQUERO_TIGHT_CROP_SAMPLE")


@pytest.mark.skipif(not (MODEL_DIR.is_dir() and TIGHT_CROP), reason="needs models/buffalo_l and VAQUERO_TIGHT_CROP_SAMPLE")
def test_face_filling_the_frame_is_found() -> None:
    from vaquero.engine import FaceMatcher, read_image

    img = read_image(Path(TIGHT_CROP))
    assert img is not None
    assert FaceMatcher(MODEL_DIR).embeddings(img)


def test_parse_version() -> None:
    from vaquero.remote import parse_version

    assert parse_version("v0.10.2") > parse_version("0.9.9")
    assert parse_version("v1.2.3-beta.1") == (1, 2, 3)


def test_list_media_skips_an_unreadable_subfolder_but_not_an_unreadable_root(tmp_path: Path) -> None:
    touch(tmp_path / "a.jpg")
    locked = tmp_path / "locked"
    touch(locked / "b.jpg")
    locked.chmod(0)
    try:
        assert [p.name for p in list_media(tmp_path, include_videos=False, recursive=True)] == ["a.jpg"]
        with pytest.raises(PermissionError):
            list_media(locked, include_videos=False)
    finally:
        locked.chmod(0o755)


def test_list_media_includes_heic(tmp_path: Path) -> None:
    touch(tmp_path / "IMG_0001.HEIC")
    touch(tmp_path / "IMG_0002.heif")
    assert [p.name for p in list_media(tmp_path, include_videos=False)] == ["IMG_0001.HEIC", "IMG_0002.heif"]


@pytest.mark.skipif(shutil.which("sips") is None, reason="needs macOS sips to write a HEIC file")
def test_heic_decodes_to_bgr(tmp_path: Path) -> None:
    import cv2

    from vaquero.engine import read_image

    png, heic = tmp_path / "red.png", tmp_path / "red.heic"
    img = np.zeros((64, 96, 3), np.uint8)
    img[:, :] = (0, 0, 255)  # BGR red
    cv2.imwrite(str(png), img)
    subprocess.run(["sips", "-s", "format", "heic", str(png), "--out", str(heic)], check=True, capture_output=True)

    out = read_image(heic)
    assert out is not None
    assert out.shape == (64, 96, 3)
    b, g, r = out[32, 48]
    assert r > 200 and g < 60 and b < 60


@pytest.mark.skipif(shutil.which("sips") is None, reason="needs macOS sips to write a HEIC file")
def test_truncated_heic_does_not_raise(tmp_path: Path) -> None:
    import cv2

    from vaquero.engine import read_image

    png, heic = tmp_path / "noise.png", tmp_path / "noise.heic"
    cv2.imwrite(str(png), np.random.default_rng(0).integers(0, 255, (600, 800, 3), dtype=np.uint8))
    subprocess.run(["sips", "-s", "format", "heic", str(png), "--out", str(heic)], check=True, capture_output=True)
    data = heic.read_bytes()
    heic.write_bytes(data[: len(data) // 2])
    out = read_image(heic)
    assert out is None or out.shape[2] == 3


def test_corrupt_heic_reads_as_none(tmp_path: Path) -> None:
    from vaquero.engine import read_image

    assert read_image(touch(tmp_path / "broken.heic", b"not a heif file")) is None


def test_similarity_transform_recovers_rotation_scale_and_shift() -> None:
    from vaquero.engine import ARCFACE_DST, similarity_transform

    a, s, t = np.deg2rad(23.0), 1.7, np.array([12.5, -4.0])
    rot = s * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    src = ARCFACE_DST.astype(np.float64)
    dst = src @ rot.T + t
    m = similarity_transform(src, dst)
    assert np.allclose(m[:, :2], rot, atol=1e-4)
    assert np.allclose(m[:, 2], t, atol=1e-3)
