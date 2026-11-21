"""Transfers across volumes and into a full or vanishing destination, on a real second volume (a RAM disk)."""

import csv
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from vaquero import engine
from vaquero.engine import Conflict, Transfer, TransferCounts, TransferStopped, transfer_matches

DISK_MB = 4


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def volume() -> Iterator[Path]:
    """A small HFS+ RAM disk mounted under /Volumes: a separate device, so moves onto it are copies, and full fast."""
    dev = subprocess.run(
        ["hdiutil", "attach", "-nomount", f"ram://{DISK_MB * 2048}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    name = f"VaqueroTest{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(["diskutil", "erasevolume", "HFS+", name, dev], capture_output=True, check=True)
        yield Path("/Volumes") / name
    finally:
        subprocess.run(["hdiutil", "detach", dev, "-force"], capture_output=True, check=False)


def rows(journal: Path) -> list[list[str]]:
    return list(csv.reader(journal.open())) if journal.exists() else []


def test_overwrite_keeps_both_same_named_matches_from_one_batch(tmp_path: Path) -> None:
    """Two matches named a.jpg in different subfolders: Overwrite replaces the file that was there, never the
    first match it just moved."""
    a1 = touch(tmp_path / "src" / "one" / "a.jpg", b"first")
    a2 = touch(tmp_path / "src" / "two" / "A.JPG", b"second")
    dest = tmp_path / "dest"
    touch(dest / "a.jpg", b"old")
    journal = tmp_path / "moves.csv"

    assert transfer_matches([a1, a2], dest, journal, conflict=Conflict.OVERWRITE) == (2, 0, 0)

    assert (dest / "a.jpg").read_bytes() == b"first"
    assert (dest / "A 2.JPG").read_bytes() == b"second"
    assert [r[2] for r in rows(journal)] == ["moved, overwrote", "moved, renamed"]


def test_cross_volume_move(tmp_path: Path, volume: Path) -> None:
    a = touch(tmp_path / "src" / "a.jpg", b"new")
    journal = tmp_path / "moves.csv"

    assert transfer_matches([a], volume / "out", journal) == (1, 0, 0)

    assert (volume / "out" / "a.jpg").read_bytes() == b"new"
    assert not a.exists()
    assert not list((volume / "out").glob(".*vaquero-part"))
    assert rows(journal) == [[str(a), str(volume / "out" / "a.jpg"), "moved"]]


@pytest.mark.parametrize("conflict", [Conflict.OVERWRITE, Conflict.KEEP_BOTH])
@pytest.mark.parametrize("mode", [Transfer.MOVE, Transfer.COPY])
def test_full_destination_keeps_the_old_file_and_the_source(
    tmp_path: Path, volume: Path, conflict: Conflict, mode: Transfer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy runs out of space halfway (the disk filled up from elsewhere after the space check): the file it
    would replace, and the source, are both untouched, no partial file is left under any name, and the batch stops
    instead of failing every later file."""
    monkeypatch.setattr(engine, "_check_space", lambda *args: None)
    dest = volume / "out"
    touch(dest / "a.jpg", b"old")
    big = touch(tmp_path / "src" / "a.jpg", b"\xff" * (DISK_MB * 2 << 20))
    later = touch(tmp_path / "src" / "b.jpg")
    journal = tmp_path / "moves.csv"

    with pytest.raises(TransferStopped) as stopped:
        transfer_matches([big, later], dest, journal, conflict=conflict, mode=mode)

    assert stopped.value.counts == (0, 1, 0)
    assert "cannot take more files" in str(stopped.value)
    assert (dest / "a.jpg").read_bytes() == b"old"
    assert big.stat().st_size == DISK_MB * 2 << 20 and later.exists()
    assert sorted(p.name for p in dest.iterdir()) == ["a.jpg"]
    assert rows(journal) == []


def test_vanished_destination_stops_the_batch(tmp_path: Path) -> None:
    files = [touch(tmp_path / "src" / f"{n}.jpg") for n in "abc"]
    dest = tmp_path / "dest"
    journal = tmp_path / "moves.csv"

    def unplug(done: int, total: int) -> None:
        if done == 1:
            shutil.rmtree(dest)

    with pytest.raises(TransferStopped) as stopped:
        transfer_matches(files, dest, journal, unplug, mode=Transfer.COPY)

    assert stopped.value.counts == (1, 1, 0)
    assert "no longer available" in str(stopped.value)
    assert all(f.exists() for f in files)


def test_each_journal_row_is_on_disk_before_the_next_file(tmp_path: Path) -> None:
    files = [touch(tmp_path / "src" / f"{n}.jpg") for n in "abc"]
    journal = tmp_path / "moves.csv"
    seen: list[int] = []

    transfer_matches(files, tmp_path / "dest", journal, lambda done, total: seen.append(len(rows(journal))))

    assert seen == [0, 1, 2, 3]


def test_move_whose_source_cannot_be_removed_is_journaled_as_a_copy(tmp_path: Path, volume: Path) -> None:
    a = touch(tmp_path / "src" / "a.jpg", b"new")
    journal = tmp_path / "moves.csv"
    a.parent.chmod(0o555)
    try:
        assert transfer_matches([a], volume / "out", journal) == (1, 0, 0)
    finally:
        a.parent.chmod(0o755)

    assert a.exists() and (volume / "out" / "a.jpg").read_bytes() == b"new"
    assert rows(journal) == [[str(a), str(volume / "out" / "a.jpg"), "copied"]]


def test_leftover_partial_copies_are_cleared(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    touch(dest / ".a.jpg.vaquero-part")
    touch(dest / ".other-hidden")
    b = touch(tmp_path / "src" / "b.jpg")

    transfer_matches([b], dest, tmp_path / "moves.csv", mode=Transfer.COPY)

    assert sorted(p.name for p in dest.iterdir()) == [".other-hidden", "b.jpg"]


def test_running_a_stopped_copy_again_skips_what_it_already_did(tmp_path: Path) -> None:
    files = [touch(tmp_path / "src" / f"{n}.jpg", n.encode() * 10) for n in "abc"]
    dest = tmp_path / "dest"
    journal = tmp_path / "moves.csv"
    transfer_matches(files[:2], dest, journal, mode=Transfer.COPY)

    assert transfer_matches(files, dest, journal, mode=Transfer.COPY) == TransferCounts(1, 0, 2)

    assert sorted(p.name for p in dest.iterdir()) == ["a.jpg", "b.jpg", "c.jpg"]
    assert len(rows(journal)) == 3


def test_a_different_photo_with_the_same_name_is_not_taken_for_a_copy(tmp_path: Path) -> None:
    a = touch(tmp_path / "src" / "a.jpg", b"the person")
    dest = tmp_path / "dest"
    touch(dest / "a.jpg", b"someone else entirely")

    assert transfer_matches([a], dest, tmp_path / "moves.csv", mode=Transfer.COPY) == (1, 0, 0)

    assert (dest / "a 2.jpg").read_bytes() == b"the person"


class _StubMatcher:
    """Scores every image 0.5, and raises a non-OSError (as onnxruntime does) on the one marked bad."""

    def best_score(self, frames: object) -> tuple[float, int, int]:
        frame = next(iter(frames))  # type: ignore[call-overload]
        if frame[0, 0, 0] == 255:
            raise RuntimeError("onnxruntime: Non-zero status code returned")
        return 0.5, 1, 1


def _image(path: Path, value: int) -> Path:
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((8, 8, 3), value, np.uint8))
    return path


def test_one_file_failing_in_the_model_does_not_end_the_scan(tmp_path: Path) -> None:
    from vaquero.engine import scan

    files = [_image(tmp_path / "a.png", 10), _image(tmp_path / "bad.png", 255), _image(tmp_path / "c.png", 10)]

    results = list(scan(_StubMatcher(), files))  # type: ignore[arg-type]

    assert [r.path.name for r in results] == ["a.png", "c.png"]


def test_a_vanished_scan_folder_stops_the_scan_with_the_results_so_far(tmp_path: Path) -> None:
    from vaquero.engine import SourceGone, scan

    folder = tmp_path / "share"
    files = [_image(folder / f"{n}.png", 10) for n in "ab"] + [folder / "c.png", folder / "d.png"]
    got = []

    with pytest.raises(SourceGone, match="no longer available"):
        for r in scan(_StubMatcher(), files, gone=lambda: not (folder / "a.png").exists()):  # type: ignore[arg-type]
            got.append(r.path.name)
            if len(got) == 2:
                shutil.rmtree(folder)

    assert got == ["a.png", "b.png"]


def test_a_destination_too_small_stops_before_the_first_file(tmp_path: Path, volume: Path) -> None:
    files = [touch(tmp_path / "src" / f"{n}.jpg", b"\xff" * (2 << 20)) for n in "abc"]
    journal = tmp_path / "moves.csv"

    with pytest.raises(TransferStopped, match="nothing was moved or copied") as stopped:
        transfer_matches(files, volume / "out", journal)

    assert stopped.value.counts == (0, 0, 0)
    assert all(f.exists() for f in files) and not list((volume / "out").iterdir())


def test_listing_can_be_cancelled_and_reports_its_count(tmp_path: Path) -> None:
    from vaquero.engine import list_media

    for d in range(5):
        touch(tmp_path / f"d{d}" / "a.jpg")
    counts: list[int] = []

    found = list_media(tmp_path, False, recursive=True, cancelled=lambda: len(counts) >= 2, found_so_far=counts.append)

    assert counts[:2] == [0, 1] and len(found) == 1
