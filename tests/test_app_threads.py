import gc
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QSettings, QTimer
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


def _window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "ui.Window":
    ini = str(tmp_path / "vaquero.ini")
    monkeypatch.setattr(ui, "QSettings", lambda *_: QSettings(ini, QSettings.Format.IniFormat))
    QApplication.instance() or QApplication([])
    return ui.Window()


class _OnMain(QObject):
    """A receiver living on the main thread: a queued call to a bare lambda would run on the worker's thread, which
    is already winding down, whereas the window's own handlers run on the main thread."""

    def __init__(self, fn: object) -> None:
        super().__init__()
        self.fn = fn

    def call(self, *args: object) -> None:
        self.fn(*args)  # type: ignore[operator]


def _wait_idle(w: "ui.Window", ms: int = 5000) -> None:
    qa = QApplication.instance()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(qa.quit)
    deadline.start(ms)
    poll = QTimer()
    poll.timeout.connect(lambda: w.job is None and qa.quit())
    poll.start(10)
    qa.exec()
    poll.stop()
    deadline.stop()


def test_execute_is_enabled_after_a_scan_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The finished handler runs while the job is still recorded; it must count as over."""
    w = _window(tmp_path, monkeypatch)
    w.scan_root = tmp_path
    hit = ui.ScanResult(tmp_path / "a.jpg", "image", 0.99, 1, 1)

    class Scan(ui.TaskWorker):
        def run(self) -> None:
            self._emit_end(self.finished, None)

    job = Scan(lambda progress, cancelled: None)
    handler = _OnMain(lambda _: w._on_finished([hit], False, 1, ""))
    job.finished.connect(handler.call, ui.QUEUED)
    w._run(job, "a scan")
    _wait_idle(w)

    assert w.execute_btn.isEnabled()
    assert w.scan_btn.isEnabled() == bool(w.packs)


def test_a_finished_handler_can_start_the_next_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The update check's handler starts the update download; that second job must not be refused."""
    w = _window(tmp_path, monkeypatch)
    ran: list[str] = []
    second = ui.TaskWorker(lambda progress, cancelled: ran.append("download"))
    first = ui.TaskWorker(lambda progress, cancelled: ran.append("check"))
    handler = _OnMain(lambda _: w._run(second, "the update download"))
    first.finished.connect(handler.call, ui.QUEUED)
    w._run(first)
    _wait_idle(w)
    _wait_idle(w)

    assert ran == ["check", "download"]


def test_a_failed_model_download_offers_try_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _window(tmp_path, monkeypatch)
    calls: list[str] = []

    def fail(progress: object, cancelled: object) -> None:
        raise ui.RemoteError("could not reach GitHub: offline")

    w._run(ui.TaskWorker(fail), "the face model download", retry=lambda: calls.append("again"))
    _wait_idle(w)

    assert 'href="retry"' in w.status.text() and "could not reach GitHub" in w.status.text()
    w._on_status_link("retry")
    assert calls == ["again"]
    w._on_status_link("retry")  # used up: a second click does nothing
    assert calls == ["again"]


def test_a_job_keeps_the_mac_awake_only_while_it_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _window(tmp_path, monkeypatch)
    w._run(ui.TaskWorker(lambda progress, cancelled: None), "a scan")
    assert w.activity is not None and w.activity.active
    _wait_idle(w)
    assert w.activity is None


def test_the_launch_update_check_waits_for_the_model_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading as th

    w = _window(tmp_path, monkeypatch)
    checks: list[bool] = []
    downloading = th.Event()  # the download runs until the check has been asked for, as the real one does
    w._run(ui.TaskWorker(lambda progress, cancelled: downloading.wait(5)), "the face model download")
    w.check_updates(quiet=True)  # busy: deferred
    monkeypatch.setattr(w, "check_updates", lambda quiet=False: checks.append(quiet))
    downloading.set()
    _wait_idle(w)

    assert checks == [True]


def test_a_folder_check_that_hangs_is_reported_not_waited_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading as th

    w = _window(tmp_path, monkeypatch)
    for edit in (w.refs, w.source, w.dest):
        edit.setText(str(tmp_path))
    release = th.Event()

    def stalled(named: object, current: list[str]) -> tuple[str, list[Path]]:
        current[0] = "Folder to scan"
        release.wait(5)
        return "", []

    monkeypatch.setattr(ui, "folder_problem", stalled)
    monkeypatch.setattr(ui, "PREFLIGHT_TIMEOUT_MS", 200)
    started: list[object] = []
    w._preflight(lambda *folders: started.append(folders))
    assert not w.scan_btn.isEnabled()
    qa = QApplication.instance()
    QTimer.singleShot(600, qa.quit)
    qa.exec()
    release.set()
    QTimer.singleShot(200, qa.quit)
    qa.exec()

    assert "Folder to scan is not responding" in w.status.text()
    assert not started  # the late answer is ignored
    assert w.scan_btn.isEnabled() == bool(w.packs)


def test_folder_problems_are_found_before_a_scan(tmp_path: Path) -> None:
    ok = tmp_path / "ok"
    ok.mkdir()
    named = {"Reference faces": ok, "Folder to scan": ok, "Destination": tmp_path / "new"}
    assert ui.folder_problem(named, [""]) == ("", [])
    assert ui.folder_problem({**named, "Folder to scan": tmp_path / "nope"}, [""])[0] == "Folder to scan: not a folder"
    gone = {**named, "Destination": Path("/Volumes/NoSuchDriveXYZ/out")}
    assert "NoSuchDriveXYZ is not connected" in ui.folder_problem(gone, [""])[0]
    same = {**named, "Destination": ok}
    assert ui.folder_problem(same, [""])[0] == "Destination must differ from the folder being scanned"
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert "no permission to add files" in ui.folder_problem({**named, "Destination": locked}, [""])[0]
    finally:
        locked.chmod(0o755)
