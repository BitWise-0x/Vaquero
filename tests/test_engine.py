import csv
from pathlib import Path

from facesort.engine import list_media, move_matches, pack_models


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


def test_move_never_overwrites_and_journals(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    a = touch(src / "a.jpg", b"new")
    b = touch(src / "b.jpg")
    existing = touch(dest / "a.jpg", b"old")
    journal = tmp_path / "moves.csv"

    moved, skipped = move_matches([a, b], dest, journal)

    assert (moved, skipped) == (1, 1)
    assert existing.read_bytes() == b"old"
    assert a.exists() and not b.exists() and (dest / "b.jpg").exists()
    assert list(csv.reader(journal.open())) == [[str(b), str(dest / "b.jpg")]]
