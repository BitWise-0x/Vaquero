"""The desktop layer: menu bar icon, Dock and menu bar presence, closing and quitting, undo from the window,
the Settings window, the log's links, and moving the app into Applications."""

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication

from vaquero import access, menubar
from vaquero import app as ui
from vaquero.engine import Transfer, transfer_matches


@pytest.fixture
def w(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "ui.Window":
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    QApplication.instance() or QApplication([])
    window = ui.Window()
    window.journal = tmp_path / "moves.csv"
    yield window
    window.status_item.timer.stop()


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_progress_view_draws_an_amber_bar_and_the_line() -> None:
    assert menubar.progress_text("scan", 50, 200, "50 of 200 files") == "Scan: 50 of 200 files"
    assert menubar.fraction(5, 0) == 0.0 and menubar.fraction(300, 200) == 1.0
    QApplication.instance() or QApplication([])
    view = menubar.ProgressView()
    view.show_state(0.25, "Scan: 50 of 200 files", dark=True)
    img = view.grab().toImage()
    v = menubar.ProgressView
    bar_w = v.WIDTH - v.INSET - v.RIGHT - v.PCT_W
    y = (v.BAR_Y + v.BAR_H // 2) * img.width() // v.WIDTH
    scale = img.width() / v.WIDTH
    filled = img.pixelColor(int((v.INSET + bar_w * 0.1) * scale), y)
    empty = img.pixelColor(int((v.INSET + bar_w * 0.9) * scale), y)
    assert filled.name() == ui.theme.SIGNAL  # filled in the accent up to 25%
    assert empty.name() != ui.theme.SIGNAL


def test_icon_sweeps_while_working_and_holds_still_otherwise(w: "ui.Window") -> None:
    item = w.status_item
    assert len(item.frames) == menubar.SWEEP_FRAMES + menubar.HOLD_FRAMES
    assert item.still.isMask()  # idle: a template macOS tints for the menu bar
    assert not any(icon.isMask() for icon in item.frames)  # working: drawn in color, the amber line included
    mid = item.frames[menubar.SWEEP_FRAMES // 2].pixmap(36, 36).toImage()
    colors = {mid.pixelColor(x, y).name() for x in range(36) for y in range(36) if mid.pixelColor(x, y).alpha() > 200}
    assert any(c.startswith("#e") or c.startswith("#f") for c in colors - {"#ffffff"})  # amber in the frame
    item.set_working(True)
    assert item.working
    item.set_working(False)
    assert not item.working


def test_menu_follows_the_running_job(w: "ui.Window") -> None:
    w.job, w.task = ui.TaskWorker(lambda p, c: None), "a scan"
    w.progress_state = ("scan", 30, 120, "30 of 120 files")
    w.status.setText("Scanned: <b>30 of 120</b>")
    item = w.status_item
    item.refresh()

    assert item.progress_action.isVisible() and not item.status_action.isVisible()
    assert (item.progress_view.frac, item.progress_view.line) == (0.25, "Scan: 30 of 120 files")
    assert item.cancel_action.isEnabled() and not item.scan_action.isEnabled()
    w.job = w.task = None
    item.refresh()
    assert item.status_action.isVisible() and not item.progress_action.isVisible()
    assert item.status_action.text() == "Scanned: 30 of 120"


def test_time_left() -> None:
    assert ui.time_left(2, 10, 100) == ""  # too early to tell
    assert ui.time_left(30, 50, 100) == " · under a minute left"
    assert ui.time_left(60, 10, 100) == " · about 9 min left"
    assert ui.time_left(600, 10, 100) == " · about 1.5 h left"
    assert ui.time_left(60, 100, 100) == ""


def test_closing_with_the_menu_bar_icon_keeps_vaquero_running(w: "ui.Window", monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(w.status_item.tray, "isVisible", lambda: True)
    w.show()
    assert w.close() is False
    assert not w.isVisible()
    quits: list[bool] = []
    monkeypatch.setattr(ui.QApplication, "quit", lambda: quits.append(True))
    w.quit_app()
    assert quits == [True]


def test_a_quit_from_macos_is_not_turned_into_a_hide(w: "ui.Window", monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QEvent

    monkeypatch.setattr(w.status_item.tray, "isVisible", lambda: True)
    w.eventFilter(QApplication.instance(), QEvent(QEvent.Type.Quit))
    assert w.quitting


def test_dock_and_menu_bar_are_never_both_off(w: "ui.Window") -> None:
    w.set_presence(dock=False, menu_bar=False)
    assert w.dock_shown()
    w.set_presence(dock=False, menu_bar=True)
    assert not w.dock_shown() and w.menu_bar_shown()
    w.set_presence(dock=True, menu_bar=False)
    assert w.dock_shown() and not w.menu_bar_shown()


def test_undo_from_the_window_brings_moved_results_back(w: "ui.Window", tmp_path: Path) -> None:
    a = touch(tmp_path / "src" / "a.jpg", b"aaa")
    b = touch(tmp_path / "src" / "b.jpg", b"bbb")
    transfer_matches([a, b], tmp_path / "dest", w.journal)
    assert w.undo_text() == "Undo Move of 2 Files"
    w.dropped = [ui.ScanResult(a, "image", 0.9, 1, 1)]

    from vaquero.engine import last_run, undo_run

    counts = undo_run(last_run(w.journal), w.journal)
    w._on_undo_finished(counts, False)

    assert [r.path for r in w.results] == [a]
    assert w.undo_text() == ""


def test_undo_text_for_a_copy(w: "ui.Window", tmp_path: Path) -> None:
    transfer_matches([touch(tmp_path / "src" / "a.jpg")], tmp_path / "dest", w.journal, mode=Transfer.COPY)
    assert w.undo_text() == "Undo Copy of 1 File"


def test_settings_window_keeps_one_of_dock_and_menu_bar(w: "ui.Window") -> None:
    from vaquero.settings import SettingsDialog

    dialog = SettingsDialog(w)
    dialog.dock.setChecked(False)
    assert dialog.menu_bar.isChecked()
    dialog.menu_bar.setChecked(False)
    assert dialog.dock.isChecked()
    assert w.dock_shown()
    assert not dialog.login.isEnabled()  # from source: no app bundle to register


def test_folder_states(tmp_path: Path) -> None:
    assert access.folder_state(str(tmp_path)) == access.FolderState.OK
    assert access.folder_state("") == access.FolderState.NOT_SET
    assert access.folder_state(str(tmp_path / "nope")) == access.FolderState.MISSING
    assert access.folder_state("/Volumes/NoSuchDriveXYZ/p") == access.FolderState.NOT_CONNECTED
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert access.folder_state(str(locked)) == access.FolderState.NO_PERMISSION
    finally:
        locked.chmod(0o755)


def test_where_the_app_should_not_stay() -> None:
    assert "temporary copy" in access.move_offer_reason(Path("/private/var/folders/x/T/AppTranslocation/A/d/Vaquero.app"))
    assert "Downloads" in access.move_offer_reason(Path.home() / "Downloads" / "Vaquero.app")
    assert access.move_offer_reason(Path("/Applications/Vaquero.app")) is None


def test_moving_into_applications_replaces_an_older_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apps = tmp_path / "Applications"
    apps.mkdir()
    monkeypatch.setattr(access, "applications_folder", lambda: apps)
    new = tmp_path / "Downloads" / "Vaquero.app"
    (new / "Contents").mkdir(parents=True)
    with (new / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": "2.0.0"}, fh)
    (apps / "Vaquero.app" / "Contents").mkdir(parents=True)
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;0;Safari;", str(new)], check=True)

    moved = access.move_to_applications(new)

    assert moved == apps / "Vaquero.app"
    with (moved / "Contents" / "Info.plist").open("rb") as fh:
        assert plistlib.load(fh)["CFBundleShortVersionString"] == "2.0.0"
    attrs = subprocess.run(["xattr", str(moved)], capture_output=True, text=True).stdout
    assert "com.apple.quarantine" not in attrs


def test_log_links_open_reveal_and_point_near_what_is_gone(w: "ui.Window", tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = touch(tmp_path / "a.jpg")
    opened: list[str] = []
    revealed: list[list[str]] = []
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    monkeypatch.setattr(ui.subprocess, "run", lambda cmd, check=False: revealed.append(cmd))

    w._open_link(QUrl.fromLocalFile(str(photo)))
    w._open_link(QUrl.fromLocalFile(str(photo)), reveal=True)
    w._open_link(QUrl.fromLocalFile(str(tmp_path / "gone" / "b.jpg")))
    w._open_link(QUrl.fromLocalFile("/Volumes/NoSuchDriveXYZ/c.jpg"))

    assert opened == [str(photo)]
    assert revealed == [["open", "-R", str(photo)]]
    assert "NoSuchDriveXYZ, which is not connected" in w.status.text()


def test_menus_exist_with_their_shortcuts(w: "ui.Window") -> None:
    titles = [m.title() for m in w.menuBar().findChildren(ui.QMenu)]
    for title in ("File", "Edit", "Window", "Help"):
        assert title in titles
    assert w.scan_menu_action.shortcut().toString() == "Ctrl+R"
    assert w.cancel_menu_action.shortcut().toString() == "Ctrl+."


def test_the_quit_button_quits_even_with_the_menu_bar_icon(w: "ui.Window", monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(w.status_item.tray, "isVisible", lambda: True)
    quits: list[bool] = []
    monkeypatch.setattr(ui.QApplication, "quit", lambda: quits.append(True))
    w.show()
    w.quit_btn.click()
    assert quits == [True]


def test_right_click_on_a_log_link_offers_open_show_and_copy(w: "ui.Window", tmp_path: Path) -> None:
    from PySide6.QtCore import QPoint

    photo = touch(tmp_path / "a.jpg")
    w.log_view.setHtml(f'<a href="{QUrl.fromLocalFile(str(photo)).toString()}">a.jpg</a>')
    w.log_view.resize(400, 200)
    w.show()
    QApplication.processEvents()

    texts = [a.text() for a in w.log_view.link_menu(QPoint(8, 8)).actions() if not a.isSeparator()]

    assert texts[:3] == ["Open", "Show in Finder", "Copy Path"]
    plain = [a.text() for a in w.log_view.link_menu(QPoint(390, 190)).actions() if not a.isSeparator()]
    assert "Open" not in plain


def test_the_status_line_in_the_menu_opens_the_window(w: "ui.Window", monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[bool] = []
    monkeypatch.setattr(w, "show_window", lambda: shown.append(True))
    item = menubar.StatusItem(w, ui.asset)
    assert item.status_action.isEnabled()  # full contrast, not dimmed
    item.status_action.trigger()
    assert shown == [True]
