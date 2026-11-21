"""Reference handling, scoring and scanning with a fake detector and recognizer: no model files needed.

Each test image is a list of (face area, person) pairs. The fake detector turns them into faces and the fake
recognizer gives every person a fixed direction, so same-person faces score about 0.6 and strangers about 0.
"""

import csv
import zlib
from pathlib import Path

import numpy as np
import pytest

from vaquero import engine
from vaquero.engine import Face, FaceMatcher, list_media, scan, transfer_matches

DIM = 512
SPREAD = 0.8  # per-photo noise: two photos of one person land near 1 / (1 + SPREAD**2) similarity


class Photo(list[tuple[float, str]]):
    """A decoded image as the matcher sees it: its faces, plus the shape the padded retry reads."""

    shape = (100, 100, 3)


def _direction(name: str, salt: str = "") -> np.ndarray:
    v = np.random.default_rng(zlib.crc32(f"{name}/{salt}".encode())).standard_normal(DIM)
    return v / np.linalg.norm(v)


class FakeDetector:
    def detect(self, img: Photo) -> list[Face]:
        faces = []
        for i, (area, _person) in enumerate(img):
            side = area**0.5
            kps = np.zeros((5, 2))
            kps[0, 0] = i  # the recognizer reads the face back by its index in the photo
            faces.append(Face(bbox=np.array([0.0, 0.0, side, side]), kps=kps, score=0.9))
        return faces


class FakeRecognizer:
    def embed_many(self, img: Photo, faces: list[Face]) -> np.ndarray:
        rows = []
        for face in faces:
            person = img[int(face.kps[0, 0])][1]
            v = _direction(person) + SPREAD * _direction(person, salt=f"{id(img)}-{face.kps[0, 0]}")
            rows.append(v / np.linalg.norm(v))
        return np.stack(rows)


@pytest.fixture
def photos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Writes placeholder files and serves their faces from a table instead of decoding them."""
    table: dict[str, Photo] = {}

    def add(folder: Path, name: str, faces: list[tuple[float, str]]) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / name
        p.write_bytes(b"x")
        table[str(p)] = faces
        return p

    def read(path: Path) -> Photo | None:
        faces = table.get(str(path))
        return None if faces is None else Photo(faces)

    monkeypatch.setattr(engine, "read_image", read)
    # the padded retry for faceless images borders the photo; a fake photo has nothing to pad
    monkeypatch.setattr(engine.cv2, "copyMakeBorder", lambda img, *_, **__: img)
    return add


def matcher() -> FaceMatcher:
    m = FaceMatcher.__new__(FaceMatcher)
    m.detector, m.recognizer = FakeDetector(), FakeRecognizer()
    m.centroid = m.refs = None
    return m


def test_references_skip_faceless_photos_and_other_files(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    for i in range(3):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    photos(refs, "group.jpg", [(1600.0, "alex"), (400.0, "sam")])
    photos(refs, "landscape.jpg", [])
    photos(refs, "notes.txt", [(900.0, "alex")])

    m = matcher()
    assert m.load_references(refs) == 4
    assert m.ref_count == 4


def _reference_person(m: FaceMatcher) -> str:
    """Which fake person the reference centroid points at."""
    assert m.centroid is not None
    return max(("alex", "sam", "kim", "lee"), key=lambda p: float(m.centroid @ engine._unit(_direction(p))))


def test_group_references_use_the_person_common_to_the_photos(photos, tmp_path: Path) -> None:
    """Someone else's face is the largest in two of three photos; the shared person still wins."""
    refs = tmp_path / "refs"
    photos(refs, "alex.jpg", [(900.0, "alex")])
    photos(refs, "party.jpg", [(2500.0, "sam"), (400.0, "alex")])
    photos(refs, "trip.jpg", [(3600.0, "kim"), (900.0, "alex"), (300.0, "lee")])

    m = matcher()
    assert m.load_references(refs) == 3
    assert _reference_person(m) == "alex"


def test_references_that_are_all_group_photos_find_the_shared_person(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    photos(refs, "a.jpg", [(2500.0, "sam"), (900.0, "alex")])
    photos(refs, "b.jpg", [(2500.0, "kim"), (900.0, "alex")])
    photos(refs, "c.jpg", [(900.0, "alex"), (2500.0, "lee")])

    m = matcher()
    assert m.load_references(refs) == 3
    assert _reference_person(m) == "alex"


def test_two_people_in_every_reference_photo_is_flagged(photos, tmp_path: Path, caplog) -> None:
    refs = tmp_path / "refs"
    for i in range(3):
        photos(refs, f"couple{i}.jpg", [(900.0, "alex"), (900.0, "sam")])

    matcher().load_references(refs)
    assert "two people about equally often" in caplog.text


def test_a_single_group_photo_uses_its_largest_face(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    photos(refs, "group.jpg", [(400.0, "sam"), (2500.0, "alex")])

    m = matcher()
    assert m.load_references(refs) == 1
    assert _reference_person(m) == "alex"


def test_references_without_any_face_fail(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    photos(refs, "landscape.jpg", [])
    with pytest.raises(ValueError, match="no faces"):
        matcher().load_references(refs)


def test_a_wrong_person_among_four_or_more_references_is_dropped(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    for i in range(4):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    photos(refs, "sam.jpg", [(900.0, "sam")])
    assert matcher().load_references(refs) == 4


def test_a_wrong_person_among_three_references_is_dropped(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    for i in range(2):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    photos(refs, "sam.jpg", [(900.0, "sam")])
    assert matcher().load_references(refs) == 2


def test_best_score_takes_the_closest_face_and_counts_agreeing_references(photos, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    for i in range(4):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    m = matcher()
    m.load_references(refs)

    group = Photo([(2500.0, "sam"), (400.0, "alex"), (900.0, "kim")])
    score, faces, agree = m.best_score(iter([group]))
    assert faces == 3
    assert score > 0.5
    assert agree == 4
    stranger, _, none_agree = m.best_score(iter([Photo([(900.0, "sam")])]))
    assert stranger < 0.2
    assert none_agree == 0


def test_scan_keeps_file_order_and_skips_unreadable_files(photos, tmp_path: Path) -> None:
    refs, src = tmp_path / "refs", tmp_path / "src"
    for i in range(4):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    names = [f"img{i:02}.jpg" for i in range(20)]
    for i, name in enumerate(names):
        photos(src, name, [(900.0, "alex" if i % 3 == 0 else "sam")])
    (src / "broken.jpg").write_bytes(b"x")  # in the folder, not in the table: reads as None
    m = matcher()
    m.load_references(refs)

    results = list(scan(m, list_media(src, include_videos=False)))
    assert [r.path.name for r in results] == sorted(names + ["broken.jpg"])
    broken = next(r for r in results if r.path.name == "broken.jpg")
    assert (broken.faces, broken.score) == (0, 0.0)
    matched = {r.path.name for r in results if r.score >= 0.35}
    assert matched == {n for i, n in enumerate(names) if i % 3 == 0}


def test_scan_stops_when_cancelled(photos, tmp_path: Path) -> None:
    refs, src = tmp_path / "refs", tmp_path / "src"
    photos(refs, "alex.jpg", [(900.0, "alex")])
    for i in range(30):
        photos(src, f"img{i:02}.jpg", [(900.0, "sam")])
    m = matcher()
    m.load_references(refs)

    seen = 0

    def cancelled() -> bool:
        return seen >= 5

    for _ in scan(m, list_media(src, include_videos=False), cancelled=cancelled):
        seen += 1
    assert seen == 5


def test_references_to_moved_matches_end_to_end(photos, tmp_path: Path) -> None:
    """References, scan, threshold, then Execute (move) with its undo journal."""
    refs, src, dest = tmp_path / "refs", tmp_path / "src", tmp_path / "src" / "Found"
    for i in range(4):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    photos(src, "alex_solo.jpg", [(900.0, "alex")])
    photos(src, "alex_in_group.jpg", [(2500.0, "sam"), (400.0, "alex")])
    photos(src, "sam.jpg", [(900.0, "sam")])
    photos(src / "2024", "alex_nested.jpg", [(900.0, "alex")])
    photos(dest, "alex_already_found.jpg", [(900.0, "alex")])
    m = matcher()
    m.load_references(refs)

    results = list(scan(m, list_media(src, include_videos=False, recursive=True, exclude=dest)))
    assert "alex_already_found.jpg" not in {r.path.name for r in results}
    matches = [r.path for r in results if r.score >= 0.35]
    assert sorted(p.name for p in matches) == ["alex_in_group.jpg", "alex_nested.jpg", "alex_solo.jpg"]

    journal = tmp_path / "moves.csv"
    assert transfer_matches(matches, dest, journal) == (3, 0, 0)
    assert sorted(p.name for p in dest.iterdir()) == [
        "alex_already_found.jpg",
        "alex_in_group.jpg",
        "alex_nested.jpg",
        "alex_solo.jpg",
    ]
    assert (src / "sam.jpg").exists()
    assert [row[2] for row in csv.reader(journal.open())] == ["moved"] * 3


def test_scan_worker_writes_the_report(photos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's scan job: references, a recursive scan that skips the destination, and the CSV report."""
    from vaquero import app as ui

    refs, src, dest, report = tmp_path / "refs", tmp_path / "src", tmp_path / "src" / "Found", tmp_path / "scan.csv"
    for i in range(4):
        photos(refs, f"alex{i}.jpg", [(900.0, "alex")])
    photos(src, "alex.jpg", [(900.0, "alex")])
    photos(src / "2024", "sam.jpg", [(900.0, "sam")])
    photos(dest, "old.jpg", [(900.0, "alex")])
    monkeypatch.setattr(ui, "FaceMatcher", lambda _model_dir: matcher())

    worker = ui.ScanWorker(tmp_path / "models", refs, src, dest, False, True, 10, report)
    done: list[tuple[list, bool, int]] = []
    worker.finished.connect(lambda results, cancelled, kept: done.append((results, cancelled, kept)))
    worker.failed.connect(pytest.fail)
    worker.run()

    results, cancelled, kept = done[0]
    assert (len(results), cancelled, kept) == (2, False, 4)
    rows = list(csv.DictReader(report.open()))
    assert [(r["file"], r["faces"], r["ref_agree"]) for r in rows] == [("2024/sam.jpg", "1", "0/4"), ("alex.jpg", "1", "4/4")]
