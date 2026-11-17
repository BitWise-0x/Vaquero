import gc
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
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
