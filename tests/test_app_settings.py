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


def test_permission_error_says_where_to_allow_access() -> None:
    msg = ui.blocked_message(PermissionError(1, "Operation not permitted", "/Volumes/Drive/faces"))
    assert "/Volumes/Drive/faces" in msg
    assert "Privacy & Security" in msg


def test_blocked_folders_lists_only_unreadable_ones(tmp_path: Path) -> None:
    open_dir, locked = tmp_path / "open", tmp_path / "locked"
    open_dir.mkdir()
    locked.mkdir()
    locked.chmod(0)
    try:
        assert ui.blocked_folders([open_dir, locked, tmp_path / "missing"]) == [locked]
    finally:
        locked.chmod(0o755)


def test_header_icon_sweeps_until_stopped() -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    moving = ui.ScanIcon(64)
    assert moving.timer.isActive()
    moving.grab()  # paints a frame of the sweep
    moving.stop()
    assert not moving.timer.isActive()
