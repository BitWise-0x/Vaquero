from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from vaquero import app as ui


def _ini(path: Path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_legacy_settings_copy_into_an_empty_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _ini(tmp_path / "old.ini")
    old.setValue("refs", "/Users/you/Pictures/People/Alex")
    old.setValue("threshold", 0.4)
    old.sync()
    monkeypatch.setattr(ui, "QSettings", lambda *_: _ini(tmp_path / "old.ini"))
    new = _ini(tmp_path / "new.ini")

    ui.migrate_settings(new)

    assert new.value("refs") == "/Users/you/Pictures/People/Alex"
    assert float(new.value("threshold")) == 0.4
    assert _ini(tmp_path / "new.ini").value("refs") == "/Users/you/Pictures/People/Alex"


def test_a_store_already_in_use_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _ini(tmp_path / "old.ini")
    old.setValue("refs", "old")
    old.sync()
    monkeypatch.setattr(ui, "QSettings", lambda *_: _ini(tmp_path / "old.ini"))
    new = _ini(tmp_path / "new.ini")
    new.setValue("refs", "current")

    ui.migrate_settings(new)

    assert new.value("refs") == "current"
