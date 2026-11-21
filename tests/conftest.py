import shutil
from pathlib import Path

import pytest

from vaquero import macos


@pytest.fixture(autouse=True)
def fake_trash(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Overwrites and undo send files to the Trash; tests use a folder of their own instead of the user's Trash."""
    trash = tmp_path_factory.mktemp("Trash")

    def to_trash(path: str) -> str:
        target = trash / f"{len(list(trash.iterdir()))}-{Path(path).name}"
        shutil.move(path, target)
        return str(target)

    monkeypatch.setattr(macos, "trash", to_trash)
    return trash
