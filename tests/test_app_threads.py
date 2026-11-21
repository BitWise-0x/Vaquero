import gc
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from vaquero import app as ui

JOBS = 1000


def test_short_jobs_back_to_back_do_not_crash_or_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launch update check is a job that ends within milliseconds; its cleanup once segfaulted at random."""
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    qa = QApplication.instance() or QApplication([])
    w = ui.Window()
    left = [JOBS]

    def tick() -> None:
        if w.job is not None:
            return
        if left[0] == 0:
            qa.quit()
            return
        left[0] -= 1
        w._run(ui.TaskWorker(lambda progress, cancelled: None))

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(0)
    qa.exec()
    timer.stop()

    assert left[0] == 0
    gc.collect()
    assert not [o for o in gc.get_objects() if isinstance(o, ui.TaskWorker)]


def test_quit_asks_before_stopping_a_running_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    QApplication.instance() or QApplication([])
    w = ui.Window()
    asked: list[str] = []
    monkeypatch.setattr(ui.QMessageBox, "exec", lambda box: asked.append(box.text()) or 0)  # closed: keep running
    job = ui.TaskWorker(lambda progress, cancelled: None)
    w.job, w.task = job, "a scan"

    event = QCloseEvent()
    w.closeEvent(event)
    w.cancel_job()

    assert asked == ["A scan is in progress.", "A scan is in progress."]
    assert not event.isAccepted()
    assert not job._cancel
    w.job = w.task = None


def test_scan_stays_off_without_a_face_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    QApplication.instance() or QApplication([])
    w = ui.Window()
    w.packs = {}
    w._set_busy(False)  # a failed or cancelled model download ends here
    assert not w.scan_btn.isEnabled()


def test_a_second_job_does_not_start_while_one_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the threshold during a move once re-enabled Execute, and a second transfer orphaned the first."""
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    QApplication.instance() or QApplication([])
    w = ui.Window()
    running = ui.TaskWorker(lambda progress, cancelled: None)
    w.job, w.task = running, "a move"
    w.results = [ui.ScanResult(tmp_path / "a.jpg", "image", 0.99, 1, 1)]

    w.threshold.setValue(w.threshold.value() - 0.01)
    w._run(ui.TaskWorker(lambda progress, cancelled: None), "another move")

    assert not w.execute_btn.isEnabled()
    assert w.job is running and w.task == "a move"
    w.job = w.task = None


def test_a_task_that_raises_anything_ends_and_frees_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A KeyError once escaped TaskWorker: the thread never finished and quit hung for 30 s, then refused."""
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    qa = QApplication.instance() or QApplication([])
    w = ui.Window()

    def boom(progress: object, cancelled: object) -> None:
        raise KeyError("tag_name")

    w._run(ui.TaskWorker(boom), "the update check")
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(qa.quit)
    deadline.start(5000)
    poll = QTimer()
    poll.timeout.connect(lambda: w.job is None and qa.quit())
    poll.start(10)
    qa.exec()
    poll.stop()

    assert w.job is None
    assert w.status.text().startswith("Failed: Something went wrong")
    assert w.scan_btn.isEnabled() == bool(w.packs)
