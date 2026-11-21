import errno
import os
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


def test_only_privacy_blocks_count_as_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """EPERM is macOS privacy control, fixed in System Settings; EACCES is file permissions, which Settings cannot
    fix, so it must not open the access walkthrough."""
    open_dir, locked, private = tmp_path / "open", tmp_path / "locked", tmp_path / "private"
    for d in (open_dir, locked, private):
        d.mkdir()
    locked.chmod(0)
    real_scandir = os.scandir

    def scandir(path: object) -> object:
        if Path(str(path)) == private:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        return real_scandir(path)

    monkeypatch.setattr(ui.os, "scandir", scandir)
    try:
        assert ui.blocked_folders([open_dir, locked, private, tmp_path / "missing"]) == [private]
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
