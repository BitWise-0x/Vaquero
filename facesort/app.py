"""Single-window GUI: reference faces, folder to scan, destination; scan, then move."""

from __future__ import annotations

import csv
import html
import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
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
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
)

from facesort import __version__
from facesort.engine import DEFAULT_VIDEO_FRAMES, Conflict, FaceMatcher, ScanResult, list_media, move_matches, pack_models, scan

log = logging.getLogger("facesort")

DEFAULT_THRESHOLD = 0.35
# matches below this are lookalike territory: logged as "review" so they get a look before Execute move
REVIEW_BELOW = 0.45
CLOSE_WAIT_MS = 30_000
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
        self.record_emitted.emit(colorize(record))


# mid-tone colors stay readable on both the light and dark macOS appearance
LEVEL_COLORS = {logging.WARNING: "#d08700", logging.ERROR: "#e5484d"}
TIME_COLOR = "#8b8d98"
MATCH_COLOR = "#30a46c"
DONE_COLOR = "#0090ff"


def colorize(record: logging.LogRecord) -> str:
    """One log line as HTML: dim time, level-colored text, green matches, bold blue completions."""
    stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
    msg = html.escape(record.getMessage())
    level = html.escape(record.levelname)
    if record.levelno >= logging.WARNING:
        body = f'<span style="color:{LEVEL_COLORS.get(record.levelno, LEVEL_COLORS[logging.ERROR])}">{level} {msg}</span>'
    elif msg.startswith("match "):
        body = f'<span style="color:{MATCH_COLOR}">{msg}</span>'
    elif msg.startswith("review "):
        body = f'<span style="color:{LEVEL_COLORS[logging.WARNING]}">{msg}</span>'
    elif " done:" in msg:
        body = f'<b style="color:{DONE_COLOR}">{msg}</b>'
    else:
        body = msg
    return f'<span style="color:{TIME_COLOR}">{stamp}</span> {body}'


class ScanWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(list, bool, int)  # list[ScanResult], cancelled, reference photos kept
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
        self.ref_count = 0

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            matcher = FaceMatcher(self.model_dir)
            matcher.load_references(self.refs)
            self.ref_count = matcher.ref_count
            if self._cancel:
                log.warning("scan cancelled before scanning")
                self.finished.emit([], True, self.ref_count)
                return
            total = len(list_media(self.folder, self.include_videos, self.recursive, self.dest))
            log.info("scanning %d files in %s", total, self.folder)
            results: list[ScanResult] = []
            with self.report.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["file", "kind", "score", "faces", "ref_agree"])
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
                    w.writerow(
                        [r.path.relative_to(self.folder), r.kind, f"{r.score:.3f}", r.faces, f"{r.agree}/{self.ref_count}"]
                    )
                    self.progress.emit(len(results), total)
            if self._cancel:
                log.warning("scan cancelled after %d of %d files", len(results), total)
            self.finished.emit(results, self._cancel, self.ref_count)
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.exception("scan failed")
            self.failed.emit(str(e))


class MoveWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(int, int, bool)  # moved, failed, cancelled
    failed = Signal(str)

    def __init__(self, paths: list[Path], dest: Path, journal: Path, conflict: Conflict) -> None:
        super().__init__()
        self.paths, self.dest, self.journal, self.conflict = paths, dest, journal, conflict
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            moved, failed = move_matches(
                self.paths, self.dest, self.journal, self.progress.emit, lambda: self._cancel, self.conflict
            )
            self.finished.emit(moved, failed, self._cancel)
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.exception("move failed")
            self.failed.emit(str(e))


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FaceSort")
        self.settings = QSettings("BitWise", "FaceSort")
        menubar = self.menuBar()
        self.settings_menu = settings_menu = menubar.addMenu("Settings")
        settings_menu.addSection("If the destination has a file with the same name")
        self.conflict_group = QActionGroup(self)
        saved_conflict = self.settings.value("conflict", Conflict.KEEP_BOTH.value, type=str)
        for value, label in ((Conflict.KEEP_BOTH, "Keep both (add a number)"), (Conflict.OVERWRITE, "Overwrite")):
            action = QAction(label, self, checkable=True)
            action.setData(value.value)
            action.setChecked(value.value == saved_conflict)
            self.conflict_group.addAction(action)
            settings_menu.addAction(action)
        if not self.conflict_group.checkedAction():
            self.conflict_group.actions()[0].setChecked(True)
        self.conflict_group.triggered.connect(lambda a: self.settings.setValue("conflict", a.data()))
        menu = menubar.addMenu("Help")
        about = QAction("About FaceSort", self)
        # macOS moves this into the application menu
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self.show_about)
        menu.addAction(about)
        settings_menu.addSeparator()
        settings_menu.addAction(about)
        self.results: list[ScanResult] = []
        self.thread: QThread | None = None
        self.job: ScanWorker | MoveWorker | None = None
        self.journal = base_dir() / "facesort-moves.csv"
        self.file_log: logging.FileHandler | None = None

        central = QWidget()
        self.setCentralWidget(central)
        grid = QGridLayout(central)
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
        # the same menu as the menu bar's, in reach without leaving the window
        self.settings_btn = QToolButton()
        self.settings_btn.setText("Settings")
        self.settings_btn.setMenu(settings_menu)
        self.settings_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for b in (self.scan_btn, self.cancel_btn, self.move_btn, self.settings_btn):
            b.setMinimumHeight(36)
            buttons.addWidget(b)
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn.clicked.connect(self.cancel_job)
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
        handler.record_emitted.connect(self.log_view.appendHtml)
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

    def _run(self, job: ScanWorker | MoveWorker) -> None:
        """Run job on a fresh thread; the thread and job are deleted once it ends."""
        thread = QThread()
        job.moveToThread(thread)
        thread.started.connect(job.run)
        # direct: QThread.quit is thread-safe, and a queued call would stall while closeEvent blocks in wait()
        job.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        job.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        job.failed.connect(self._on_failed)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self.thread, self.job = thread, job
        self.progress.setValue(0)
        self._set_busy(True)
        thread.start()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About FaceSort",
            f"<h3>FaceSort {__version__}</h3>"
            "<p>Finds every photo and video of one person in a folder by face and moves them out.</p>"
            "<p>Detection: SCRFD · Recognition: ArcFace (InsightFace model packs)<br>"
            "Inference: ONNX Runtime with Core ML on Apple Silicon</p>"
            "<p>InsightFace pretrained models are licensed for non-commercial research use only.</p>"
            '<p><a href="https://github.com/BitWise-0x/facesort">github.com/BitWise-0x/facesort</a></p>',
        )

    def _on_thread_finished(self) -> None:
        self.thread = self.job = None

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
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        self._start_log(base_dir() / f"facesort-{stamp}.log")
        report = base_dir() / f"facesort-scan-{stamp}.csv"
        log.info("scan report: %s", report)
        job = ScanWorker(
            pack,
            refs,
            source,
            dest,
            self.videos.isChecked(),
            self.subfolders.isChecked(),
            self.frames.value(),
            report,
        )
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        self._run(job)
        self.status.setText("Loading models and references")

    def cancel_job(self) -> None:
        if self.job:
            self.job.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText("Cancelling, finishing the current file")

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"Scanned {done} of {total}")

    def _start_log(self, path: Path) -> None:
        """Each scan, and the move that follows it, gets its own log file and a cleared log view."""
        if self.file_log:
            log.removeHandler(self.file_log)
            self.file_log.close()
        self.file_log = logging.FileHandler(path, encoding="utf-8")
        self.file_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(self.file_log)
        self.log_view.clear()
        log.info("FaceSort %s, log file: %s", __version__, path)

    def _done(self, text: str, cancelled: bool = False) -> None:
        self.status.setText(f"<b>{'CANCELLED' if cancelled else 'DONE'}</b>: {text}")

    def _on_finished(self, results: list[ScanResult], cancelled: bool, refs: int) -> None:
        self.results = results
        self._set_busy(False)
        self._update_match_count()
        matches = self._matches()
        review = sum(r.score < REVIEW_BELOW for r in matches)
        for r in sorted(matches, key=lambda r: -r.score):
            kind = "review" if r.score < REVIEW_BELOW else "match"
            log.info("%s %.3f refs %d/%d %s", kind, r.score, r.agree, refs, r.path)
        log.info("scan done: %d matches of %d%s", len(matches), len(results), " (cancelled)" if cancelled else "")
        if review:
            log.warning(
                "%d match%s below %.2f look like lookalike territory; check them, or raise the threshold to drop them",
                review, "" if review == 1 else "es", REVIEW_BELOW,
            )
        self._done(
            f"scanned {len(results)}, {len(matches)} matches at threshold {self.threshold.value():.2f}"
            + (f" ({review} to review)" if review else ""),
            cancelled,
        )

    def _on_failed(self, msg: str) -> None:
        self.results = [r for r in self.results if r.path.exists()]
        self._set_busy(False)
        self.status.setText(f"Failed: {msg}")

    def execute_move(self) -> None:
        dirs = self._dirs()
        matches = self._matches()
        if dirs is None or not matches:
            return
        dest = dirs[2]
        log.info("moving %d files to %s (journal %s)", len(matches), dest, self.journal)
        conflict = Conflict(self.conflict_group.checkedAction().data())
        log.info("name conflicts: %s", "keep both" if conflict is Conflict.KEEP_BOTH else "overwrite")
        job = MoveWorker([r.path for r in matches], dest, self.journal, conflict)
        job.progress.connect(self._on_move_progress)
        job.finished.connect(self._on_move_finished)
        self._run(job)

    def _on_move_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"Moved {done} of {total}")

    def _on_move_finished(self, moved: int, failed: int, cancelled: bool) -> None:
        log.info("move done: %d moved, %d failed%s", moved, failed, " (cancelled)" if cancelled else "")
        self.results = [r for r in self.results if r.path.exists()]
        self._set_busy(False)
        self._update_match_count()
        self._done(f"moved {moved}, {failed} failed. Undo list in {self.journal.name}", cancelled)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if self.job and self.thread:
            self.job.cancel()
            # a running QThread destroyed at exit aborts the process; the job stops after its current file
            if not self.thread.wait(CLOSE_WAIT_MS):
                self.status.setText("Still finishing the current file; close again in a moment")
                event.ignore()
                return
        if self.file_log:
            self.file_log.close()
        super().closeEvent(event)


def main() -> None:
    log.setLevel(logging.INFO)
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
