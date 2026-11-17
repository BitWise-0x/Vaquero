"""Single-window GUI: reference faces, folder to scan, destination; scan, then move."""

from __future__ import annotations

import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QWidget,
)

from facesort.engine import DEFAULT_VIDEO_FRAMES, FaceMatcher, ScanResult, list_media, move_matches, pack_models, scan

log = logging.getLogger("facesort")

DEFAULT_THRESHOLD = 0.35
DEFAULT_PACK = "buffalo_l"


def base_dir() -> Path:
    """Folder holding the executable: next to the .app bundle when frozen, the repo root otherwise."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent.parent
        return exe.parent
    return Path(__file__).resolve().parent.parent


def model_packs() -> dict[str, Path]:
    """Model packs bundled inside the app, then any in a models/ folder next to it (later wins)."""
    roots = [base_dir() / "models"]
    if bundled := getattr(sys, "_MEIPASS", None):
        roots.insert(0, Path(bundled) / "models")
    packs: dict[str, Path] = {}
    for root in roots:
        if root.is_dir():
            packs.update({d.name: d for d in sorted(root.iterdir()) if d.is_dir() and pack_models(d)})
    return packs


class QtLogHandler(logging.Handler, QObject):
    record_emitted = Signal(str)

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        self.record_emitted.emit(self.format(record))


class ScanWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(list)  # list[ScanResult]
    failed = Signal(str)

    def __init__(
        self,
        model_dir: Path,
        refs: Path,
        folder: Path,
        dest: Path,
        include_videos: bool,
        recursive: bool,
        frames: int,
        report: Path,
    ) -> None:
        super().__init__()
        self.model_dir, self.refs, self.folder, self.dest = model_dir, refs, folder, dest
        self.include_videos, self.recursive, self.frames, self.report = include_videos, recursive, frames, report
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            matcher = FaceMatcher(self.model_dir)
            matcher.load_references(self.refs)
            total = len(list_media(self.folder, self.include_videos, self.recursive, self.dest))
            log.info("scanning %d files in %s", total, self.folder)
            results: list[ScanResult] = []
            with self.report.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["file", "kind", "score", "faces"])
                results_iter = scan(
                    matcher,
                    self.folder,
                    self.include_videos,
                    lambda: self._cancel,
                    recursive=self.recursive,
                    exclude=self.dest,
                    video_samples=self.frames,
                )
                for r in results_iter:
                    results.append(r)
                    w.writerow([r.path.relative_to(self.folder), r.kind, f"{r.score:.3f}", r.faces])
                    self.progress.emit(len(results), total)
            if self._cancel:
                log.warning("scan cancelled after %d files", len(results))
            self.finished.emit(results)
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.exception("scan failed")
            self.failed.emit(str(e))


class Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FaceSort")
        self.settings = QSettings("BitWise", "FaceSort")
        self.results: list[ScanResult] = []
        self.thread: QThread | None = None
        self.worker: ScanWorker | None = None

        grid = QGridLayout(self)
        self.refs = self._folder_row(grid, 0, "Reference faces", "refs")
        self.source = self._folder_row(grid, 1, "Folder to scan", "source")
        self.dest = self._folder_row(grid, 2, "Move matches to", "dest")
        self.packs = model_packs()
        self.model = QComboBox()
        self.model.addItems(list(self.packs))
        saved = self.settings.value("model", DEFAULT_PACK, type=str)
        if saved in self.packs:
            self.model.setCurrentText(saved)
        self.model.currentTextChanged.connect(lambda v: self.settings.setValue("model", v))
        grid.addWidget(QLabel("Model"), 3, 0)
        grid.addWidget(self.model, 3, 1, 1, 2)

        opts = QHBoxLayout()
        self.subfolders = QCheckBox("Scan subfolders")
        self.subfolders.setChecked(self.settings.value("subfolders", False, type=bool))
        self.videos = QCheckBox("Include videos")
        self.videos.setChecked(self.settings.value("videos", True, type=bool))
        self.frames = QSpinBox()
        self.frames.setRange(1, 60)
        self.frames.setValue(self.settings.value("frames", DEFAULT_VIDEO_FRAMES, type=int))
        self.frames.setSuffix(" frames")
        self.videos.toggled.connect(self.frames.setEnabled)
        self.frames.setEnabled(self.videos.isChecked())
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.2, 0.9)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(self.settings.value("threshold", DEFAULT_THRESHOLD, type=float))
        self.threshold.valueChanged.connect(self._update_match_count)
        opts.addWidget(self.subfolders)
        opts.addWidget(self.videos)
        opts.addWidget(self.frames)
        opts.addStretch()
        opts.addWidget(QLabel("Match threshold"))
        opts.addWidget(self.threshold)
        grid.addLayout(opts, 4, 0, 1, 3)

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.cancel_btn = QPushButton("Cancel")
        self.move_btn = QPushButton("Execute move")
        self.cancel_btn.setEnabled(False)
        self.move_btn.setEnabled(False)
        for b in (self.scan_btn, self.cancel_btn, self.move_btn):
            b.setMinimumHeight(36)
            buttons.addWidget(b)
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.move_btn.clicked.connect(self.execute_move)
        grid.addLayout(buttons, 5, 0, 1, 3)

        self.progress = QProgressBar()
        grid.addWidget(self.progress, 6, 0, 1, 3)
        self.status = QLabel("Pick the folders, then Scan. Nothing moves until Execute move.")
        grid.addWidget(self.status, 7, 0, 1, 3)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        grid.addWidget(self.log_view, 8, 0, 1, 3)
        grid.setRowStretch(8, 1)
        grid.setColumnStretch(1, 1)

        handler = QtLogHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        handler.record_emitted.connect(self.log_view.appendPlainText)
        log.addHandler(handler)
        self.resize(820, 560)

    def _folder_row(self, grid: QGridLayout, row: int, label: str, key: str, default: str = "") -> QLineEdit:
        edit = QLineEdit(self.settings.value(key, default, type=str))
        btn = QPushButton("Browse")
        btn.setMinimumHeight(32)

        def pick() -> None:
            chosen = QFileDialog.getExistingDirectory(self, label, edit.text() or str(Path.home()))
            if chosen:
                edit.setText(chosen)

        btn.clicked.connect(pick)
        edit.textChanged.connect(lambda v: self.settings.setValue(key, v))
        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(edit, row, 1)
        grid.addWidget(btn, row, 2)
        return edit

    def _dirs(self) -> tuple[Path, Path, Path] | None:
        named = {
            "Reference faces": self.refs,
            "Folder to scan": self.source,
            "Move matches to": self.dest,
        }
        paths = {}
        for name, edit in named.items():
            p = Path(edit.text().strip()).expanduser()
            if not edit.text().strip() or (name != "Move matches to" and not p.is_dir()):
                self.status.setText(f"{name}: not a folder")
                return None
            paths[name] = p
        if paths["Move matches to"].resolve() == paths["Folder to scan"].resolve():
            self.status.setText("Destination must differ from the folder being scanned")
            return None
        return paths["Reference faces"], paths["Folder to scan"], paths["Move matches to"]

    def _matches(self) -> list[ScanResult]:
        t = self.threshold.value()
        return [r for r in self.results if r.score >= t]

    def _update_match_count(self) -> None:
        self.settings.setValue("threshold", self.threshold.value())
        if self.results:
            m = self._matches()
            self.status.setText(f"{len(m)} matches of {len(self.results)} scanned at threshold {self.threshold.value():.2f}")
            self.move_btn.setEnabled(bool(m))

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.move_btn.setEnabled(not busy and bool(self._matches()))

    def start_scan(self) -> None:
        dirs = self._dirs()
        if dirs is None:
            return
        refs, source, dest = dirs
        pack = self.packs.get(self.model.currentText())
        if pack is None:
            self.status.setText("No model pack found; put one in a models/ folder next to the app")
            return
        self.settings.setValue("videos", self.videos.isChecked())
        self.settings.setValue("subfolders", self.subfolders.isChecked())
        self.settings.setValue("frames", self.frames.value())
        self.results = []
        report = base_dir() / f"facesort-scan-{datetime.now():%Y%m%d-%H%M%S}.csv"
        log.info("scan report: %s", report)
        self.thread = QThread()
        self.worker = ScanWorker(
            pack,
            refs,
            source,
            dest,
            self.videos.isChecked(),
            self.subfolders.isChecked(),
            self.frames.value(),
            report,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self._set_busy(True)
        self.status.setText("Loading models and references")
        self.thread.start()

    def cancel_scan(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.status.setText("Cancelling")

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"Scanned {done} of {total}")

    def _on_finished(self, results: list[ScanResult]) -> None:
        self.results = results
        self._set_busy(False)
        self._update_match_count()
        for r in sorted(self._matches(), key=lambda r: -r.score):
            log.info("match %.3f %s", r.score, r.path)
        log.info("scan done: %d matches of %d", len(self._matches()), len(results))

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.status.setText(f"Scan failed: {msg}")

    def execute_move(self) -> None:
        dirs = self._dirs()
        matches = self._matches()
        if dirs is None or not matches:
            return
        dest = dirs[2]
        journal = base_dir() / "facesort-moves.csv"
        log.info("moving %d files to %s (journal %s)", len(matches), dest, journal)
        moved, skipped = move_matches([r.path for r in matches], dest, journal)
        log.info("move done: %d moved, %d skipped", moved, skipped)
        self.status.setText(f"Moved {moved}, skipped {skipped}. Undo pairs in {journal.name}")
        moved_paths = {r.path for r in matches if not r.path.exists()}
        self.results = [r for r in self.results if r.path not in moved_paths]
        self._update_match_count()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self.worker:
            self.worker.cancel()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(5000)
        super().closeEvent(event)


def main() -> None:
    log_file = base_dir() / "facesort.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    log.info("log file: %s", log_file)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
