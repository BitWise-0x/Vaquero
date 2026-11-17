import csv
import os
from pathlib import Path

import pytest

from facesort.engine import Conflict, list_media, move_matches, pack_models


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
    a = touch(src / "a.jpg", b"new")
    b = touch(src / "b.jpg")
    touch(dest / "a.jpg", b"old")
    touch(dest / "a 2.jpg", b"older")
    journal = tmp_path / "moves.csv"

    ticks: list[tuple[int, int]] = []
    moved, failed = move_matches([a, b], dest, journal, lambda d, t: ticks.append((d, t)))

    assert (moved, failed) == (2, 0)
    assert ticks[-1] == (2, 2)
    assert (dest / "a.jpg").read_bytes() == b"old"
    assert (dest / "a 2.jpg").read_bytes() == b"older"
    assert (dest / "a 3.jpg").read_bytes() == b"new"
    assert not a.exists() and not b.exists()
    assert list(csv.reader(journal.open())) == [
        [str(a), str(dest / "a 3.jpg"), "renamed"],
        [str(b), str(dest / "b.jpg"), "moved"],
    ]


def test_move_overwrite_replaces(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    a = touch(src / "a.jpg", b"new")
    touch(dest / "a.jpg", b"old")
    journal = tmp_path / "moves.csv"

    assert move_matches([a], dest, journal, conflict=Conflict.OVERWRITE) == (1, 0)
    assert (dest / "a.jpg").read_bytes() == b"new"
    assert sorted(p.name for p in dest.iterdir()) == ["a.jpg"]
    assert list(csv.reader(journal.open())) == [[str(a), str(dest / "a.jpg"), "overwrote"]]


MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "buffalo_l"
TIGHT_CROP = os.environ.get("FACESORT_TIGHT_CROP_SAMPLE")


@pytest.mark.skipif(not (MODEL_DIR.is_dir() and TIGHT_CROP), reason="needs models/buffalo_l and FACESORT_TIGHT_CROP_SAMPLE")
def test_face_filling_the_frame_is_found() -> None:
    from facesort.engine import FaceMatcher, read_image

    img = read_image(Path(TIGHT_CROP))
    assert img is not None
    assert FaceMatcher(MODEL_DIR).embeddings(img)


def test_parse_version() -> None:
    from facesort.remote import parse_version

    assert parse_version("v0.10.2") > parse_version("0.9.9")
    assert parse_version("v1.2.3-beta.1") == (1, 2, 3)
