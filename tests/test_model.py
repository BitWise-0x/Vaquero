"""The real face model: fetched the way the app fetches it, from the pinned model release, then run under Core ML.

With VAQUERO_MODEL_CACHE set (CI), the pack is downloaded into that folder through remote.ensure_models, which
checks the archive's and each model file's pinned SHA-256; a cached pack is reused. Otherwise the repo's
models/buffalo_l is used when present, and the tests are skipped when it is not."""

import os
from pathlib import Path

import numpy as np
import pytest

from vaquero import remote
from vaquero.engine import Face, FaceMatcher

REPO_PACK = Path(__file__).resolve().parent.parent / "models" / remote.DEFAULT_PACK


@pytest.fixture(scope="module")
def pack() -> Path:
    cache = os.environ.get("VAQUERO_MODEL_CACHE")
    if cache:
        mp = pytest.MonkeyPatch()
        support = Path(cache)
        mp.setattr(remote, "SUPPORT_DIR", support)
        mp.setattr(remote, "MODELS_DIR", support / "models")
        mp.setattr(remote, "DOWNLOADS_DIR", support / "downloads")
        support.mkdir(parents=True, exist_ok=True)
        try:
            return remote.ensure_models(lambda done, total: None)
        finally:
            mp.undo()
    if REPO_PACK.is_dir():
        return REPO_PACK
    pytest.skip("no face model: set VAQUERO_MODEL_CACHE to download it, or put it in models/buffalo_l")


def test_the_pack_matches_its_pinned_checksums(pack: Path) -> None:
    assert remote.pack_intact(pack, verify=True)


def test_the_model_runs_and_finds_no_face_in_an_empty_frame(pack: Path) -> None:
    matcher = FaceMatcher(pack)
    assert matcher.embeddings(np.zeros((480, 640, 3), np.uint8)) == []
    noise = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    assert isinstance(matcher.embeddings(noise), list)


def test_the_recognizer_gives_a_unit_length_embedding(pack: Path) -> None:
    matcher = FaceMatcher(pack)
    img = np.random.default_rng(2).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    kps = np.array([[120, 130], [180, 130], [150, 160], [125, 195], [175, 195]], np.float32)
    face = Face(bbox=np.array([90, 90, 210, 230], np.float32), kps=kps, score=0.99)

    (embedding,) = matcher.recognizer.embed_many(img, [face])

    assert embedding.shape == (512,)
    assert abs(float(np.linalg.norm(embedding)) - 1.0) < 1e-3
