"""Renders the README screenshots into docs/screenshots from the real window, fed with sample data.

Settings go to a throwaway file and no log file is written, so the saved folders of whoever runs this are
neither shown nor changed.

Run: QT_QPA_PLATFORM=offscreen uv run python scripts/screenshots.py
"""

import logging
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from vaquero import app as ui
from vaquero import theme
from vaquero.engine import ScanResult

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
HOME = Path("/Users/you/Pictures")
LOG_FILE = Path("/Users/you/Library/Logs/Vaquero/vaquero-20250301-101500.log")
REFS, SOURCE, DEST = HOME / "People" / "Alex", HOME / "Camera Roll", HOME / "People" / "Alex" / "Found"
SAMPLE = [  # name, score, reference photos agreeing
    ("2024/IMG_2231.HEIC", 0.712, 9),
    ("2024/IMG_2240.HEIC", 0.684, 9),
    ("2025/IMG_0412.JPG", 0.655, 8),
    ("2025/IMG_0977.MOV", 0.611, 8),
    ("IMG_3018.PNG", 0.574, 7),
    ("2023/IMG_8810.JPG", 0.432, 4),
]
TOTAL = 2486
REFS_KEPT = 9


def sandbox(tmp: Path) -> None:
    """Settings in a temp file; the per-scan log file handler writes nowhere."""
    path = str(tmp / "vaquero.ini")
    ui.QSettings = lambda *_: QSettings(path, QSettings.Format.IniFormat)  # type: ignore[assignment]
    no_file: Callable[..., logging.Handler] = lambda *_, **__: logging.NullHandler()
    ui.logging.FileHandler = no_file  # type: ignore[assignment]


def fill(w: ui.Window) -> None:
    w.refs.setText(str(REFS))
    w.source.setText(str(SOURCE))
    w.dest.setText(str(DEST))
    w.subfolders.setChecked(True)
    w.transfer_copy.setChecked(True)


def scan_setup(w: ui.Window) -> None:
    w._start_log(LOG_FILE)
    ui.log.info("references: %d kept of %d with a face", REFS_KEPT, REFS_KEPT)
    ui.section("Scanning")
    ui.log.info("%d files in %s", TOTAL, SOURCE, extra={"link": SOURCE, "link_text": str(SOURCE)})
    w.scan_root = SOURCE


def grab(_: QApplication, widget: object, name: str) -> None:
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))  # type: ignore[attr-defined]
    print(f"wrote {OUT / name}")


def main() -> None:
    ui.log.setLevel(logging.INFO)
    qa = QApplication(sys.argv)
    theme.apply(qa, ui.asset("check.svg").as_posix())
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        sandbox(Path(tmp))

        w = ui.Window()
        w.show()
        fill(w)

        # first launch: the model pack downloading into the log
        w.log_view.clear()
        w._banner()
        ui.section("Face model")
        ui.log.info("Downloading face detection model (%s), please wait...", ui.DEFAULT_PACK)
        QApplication.processEvents()
        w.bytes_text = "Downloading face detection model, please wait..."
        w._set_busy(True)
        w._on_bytes(118_000_000, 191_308_057)
        grab(qa, w, "first-launch.png")

        # mid-scan
        w.progress_line = None
        scan_setup(w)
        QApplication.processEvents()
        w.live_found = 0
        for done in range(1, 1490):
            w._on_progress(done, TOTAL, 0.6 if done % 300 == 0 else 0.1)
        grab(qa, w, "scanning.png")

        # scan finished, matches listed
        w.progress_line = None
        scan_setup(w)
        QApplication.processEvents()
        w._on_progress(TOTAL, TOTAL, 0.1)
        results = [ScanResult(SOURCE / name, "image", score, 1, agree) for name, score, agree in SAMPLE]
        results += [ScanResult(SOURCE / f"IMG_{i:04d}.JPG", "image", 0.05, 1, 0) for i in range(TOTAL - len(SAMPLE))]
        w._on_finished(results, False, REFS_KEPT)
        w._set_busy(False)
        w.execute_btn.setEnabled(True)
        # tall enough that the whole log, buffalo banner included, fits without scrolling
        w.resize(w.width(), 1050)
        QApplication.processEvents()
        w.log_view.verticalScrollBar().setValue(0)
        grab(qa, w, "results.png")

        def dialog_shot(name: str) -> None:
            dialog = next(x for x in qa.topLevelWidgets() if isinstance(x, QDialog) and x.isVisible())
            grab(qa, dialog, name)
            dialog.reject()

        QTimer.singleShot(300, lambda: dialog_shot("help.png"))
        w.show_help()
        # the settings dialog lists its folders; show the sample home, not the real one
        ui.LOG_DIR = LOG_FILE.parent
        ui.MODELS_DIR = Path("/Users/you/Library/Application Support/Vaquero/models")
        QTimer.singleShot(300, lambda: dialog_shot("settings.png"))
        w.show_settings()
        w.job = None
        w.close()


if __name__ == "__main__":
    main()
