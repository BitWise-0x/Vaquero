"""Single-window GUI: reference faces, folder to scan, destination; scan, then move."""

from __future__ import annotations

import csv
import html
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from facesort import __version__, remote
from facesort.remote import DEFAULT_PACK, LOG_DIR, MODELS_DIR, SUPPORT_DIR, RemoteError, parse_version
from facesort.engine import DEFAULT_VIDEO_FRAMES, Conflict, FaceMatcher, ScanResult, list_media, move_matches, pack_models, scan

log = logging.getLogger("facesort")

DEFAULT_THRESHOLD = 0.35
# matches below this are lookalike territory: logged as "review" so they get a look before Execute move
REVIEW_BELOW = 0.45
CLOSE_WAIT_MS = 30_000


def model_packs() -> dict[str, Path]:
    """Installed model packs, plus the repo's models/ when run from source (later wins)."""
    roots = [MODELS_DIR]
    if not getattr(sys, "frozen", False):
        roots.append(Path(__file__).resolve().parent.parent / "models")
    packs: dict[str, Path] = {}
    for root in roots:
        if root.is_dir():
            packs.update({d.name: d for d in sorted(root.iterdir()) if d.is_dir() and pack_models(d)})
    return packs


HELP_HTML = """
<h2>FaceSort {version}</h2>
<p>FaceSort finds every photo and video of one person in a folder and moves them to another folder.
You give it a few photos of the person. It learns their face from those, checks every face in the scanned
files against it, and lists the matches. Nothing moves until you click <b>Execute move</b>.</p>

<h3>Folders</h3>
<p><b>Reference faces</b>: a folder of photos of the person to find. Five to fifteen clear photos from different
angles work best. The largest face in each photo is used. A photo whose largest face is someone else is dropped
and noted in the log.</p>
<p><b>Folder to scan</b>: where to look for the person.</p>
<p><b>Move matches to</b>: where the matches go. Created if it does not exist. It is never scanned.</p>

<h3>Options</h3>
<p><b>Model</b>: the face model pack. <code>buffalo_l</code> is the default.</p>
<p><b>Scan subfolders</b>: also look inside folders within the scan folder. Hidden folders are skipped.
Matches from subfolders all land directly in the destination.</p>
<p><b>Include videos</b> and <b>frames</b>: scan videos too, judging each on that many frames spread evenly
through it. More frames catch a person who appears briefly, and take longer.</p>
<p><b>Name exists</b>: what happens when the destination already has a file with the same name.
<i>Keep both</i> moves the file in with a number added (<code>IMG_1 2.png</code>).
<i>Overwrite</i> replaces the existing file, which cannot be undone.</p>
<p><b>Match threshold</b>: how similar a face must be to count as a match (default {threshold:.2f}).
Raise it to drop doubtful matches, lower it to catch hard shots (small, blurred, side-on faces).
Changing it after a scan updates the matches without scanning again.</p>

<h3>Buttons</h3>
<p><b>Scan</b>: checks every file and lists the matches. Moves nothing.<br>
<b>Cancel</b>: stops a scan or a move after the current file.<br>
<b>Execute move</b>: moves the matches to the destination.<br>
<b>Quit</b>: stops anything running after the current file and closes FaceSort. A model or update download picks up where it stopped the next time.</p>

<h3>Reading the results</h3>
<p>Below the progress bar, <b>Scanned</b> counts files done and <b>Matches found</b> counts matches so far.
The line ends with <b>DONE</b> or <b>CANCELLED</b>.</p>
<p>In the log, each match shows its score and <code>refs N/M</code>: how many of the M reference photos it
matches on its own. A real match usually matches most of them (13/13); a lookalike only a few (0/13, 1/13).</p>
<p>Log colors: <span style="color:#30a46c">green</span> a match,
<span style="color:#d08700">amber</span> a <i>review</i> match (score below {review:.2f}, where lookalikes appear) or a warning,
<span style="color:#e5484d">red</span> an error, <b style="color:#0090ff">blue</b> a finished scan or move.</p>

<h3>Files</h3>
<p>In <code>~/Library/Logs/FaceSort</code> (<b>Show logs folder</b> below):<br>
<code>facesort-&lt;time&gt;.log</code>: the log of one scan and its move.<br>
<code>facesort-scan-&lt;time&gt;.csv</code>: every scanned file with its score.</p>
<p>In <code>~/Library/Application Support/FaceSort</code>:<br>
<code>facesort-moves.csv</code>: every moved file's old and new location, for undoing a move.<br>
<code>models/</code>: the face model, downloaded on first launch.</p>

<h3>Updates</h3>
<p><b>Check for updates</b> below compares this version with the latest release, then downloads it and
restarts FaceSort. It uses the GitHub CLI (<code>gh</code>) logged in on this Mac.</p>
"""


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
    progress = Signal(int, int, float)  # done, total, score of the file just scanned
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
                    self.progress.emit(len(results), total, r.score)
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


class TaskWorker(QObject):
    """Runs fn(progress, cancelled) on a worker thread: model download, update check, update download."""

    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int, int], None], Callable[[], bool]], object]) -> None:
        super().__init__()
        self.fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.finished.emit(self.fn(self.progress.emit, lambda: self._cancel))
        except (RemoteError, OSError, subprocess.CalledProcessError) as e:
            log.error("%s", e)
            self.failed.emit(str(e))


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FaceSort")
        self.settings = QSettings("BitWise", "FaceSort")
        menu = self.menuBar().addMenu("Help")
        about = QAction("About FaceSort", self)
        # macOS moves this into the application menu
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self.show_about)
        menu.addAction(about)
        self.results: list[ScanResult] = []
        self.thread: QThread | None = None
        self.job: ScanWorker | MoveWorker | TaskWorker | None = None
        self.journal = SUPPORT_DIR / "facesort-moves.csv"
        self.file_log: logging.FileHandler | None = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Help: what FaceSort does and what each setting means")
        help_btn.setFixedSize(32, 32)
        help_btn.clicked.connect(self.show_help)
        top.addWidget(help_btn)
        top.addStretch()
        outer.addLayout(top)
        grid = QGridLayout()
        outer.addLayout(grid, 1)
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
        self.conflict = QComboBox()
        self.conflict.addItem("Keep both", Conflict.KEEP_BOTH.value)
        self.conflict.addItem("Overwrite", Conflict.OVERWRITE.value)
        self.conflict.setToolTip("When the destination already has a file with the same name: keep both, the moved one numbered (name 2.jpg), or overwrite it")
        saved_conflict = self.conflict.findData(self.settings.value("conflict", Conflict.KEEP_BOTH.value, type=str))
        self.conflict.setCurrentIndex(max(saved_conflict, 0))
        self.conflict.currentIndexChanged.connect(lambda _: self.settings.setValue("conflict", self.conflict.currentData()))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.2, 0.9)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(self.settings.value("threshold", DEFAULT_THRESHOLD, type=float))
        self.threshold.valueChanged.connect(self._update_match_count)
        opts.addWidget(self.subfolders)
        opts.addWidget(self.videos)
        opts.addWidget(self.frames)
        opts.addWidget(QLabel("Name exists"))
        opts.addWidget(self.conflict)
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
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.close)
        for b in (self.scan_btn, self.cancel_btn, self.move_btn):
            b.setMinimumHeight(36)
            buttons.addWidget(b)
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.move_btn.clicked.connect(self.execute_move)
        grid.addLayout(buttons, 5, 0, 1, 3)

        self.progress = QProgressBar()
        grid.addWidget(self.progress, 6, 0, 1, 3)
        self.status = QLabel("Pick the folders, then Scan. Nothing moves until Execute move.")
        self.found = QLabel()
        self.found.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.found)
        grid.addLayout(status_row, 7, 0, 1, 3)
        self.live_found = 0
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        grid.addWidget(self.log_view, 8, 0, 1, 3)
        grid.setRowStretch(8, 1)
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.quit_btn.setMinimumHeight(36)
        bottom.addWidget(self.quit_btn)
        grid.addLayout(bottom, 9, 0, 1, 3)
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
            self._show_found(len(m))
            self.status.setText(f"{len(m)} matches of {len(self.results)} scanned at threshold {self.threshold.value():.2f}")
            self.move_btn.setEnabled(bool(m))

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.move_btn.setEnabled(not busy and bool(self._matches()))

    def _run(self, job: ScanWorker | MoveWorker | TaskWorker) -> None:
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

    def show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("FaceSort Help")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(HELP_HTML.format(version=__version__, review=REVIEW_BELOW, threshold=DEFAULT_THRESHOLD))
        layout.addWidget(text)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        updates = close.addButton("Check for updates", QDialogButtonBox.ButtonRole.ActionRole)
        logs = close.addButton("Show logs folder", QDialogButtonBox.ButtonRole.ActionRole)
        updates.clicked.connect(lambda: (dialog.accept(), self.check_updates()))
        logs.clicked.connect(self.show_logs)
        layout.addWidget(close)
        dialog.resize(620, 640)
        dialog.exec()

    def show_logs(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(LOG_DIR)], check=False)

    def ensure_models(self) -> None:
        """First launch: fetch the face model pack from the repo before anything can be scanned."""
        if self.packs:
            return
        job = TaskWorker(remote.ensure_models)
        job.progress.connect(
            lambda done, total: self._on_bytes(done, total, "Downloading face detection model, please wait...")
        )
        job.finished.connect(self._on_models_ready)
        self._run(job)
        self.status.setText("Downloading face detection model, please wait...")
        log.info("first launch: downloading the %s model pack", DEFAULT_PACK)

    def _on_bytes(self, done: int, total: int, text: str) -> None:
        self.progress.setMaximum(max(total // 1024, 1))
        self.progress.setValue(done // 1024)
        self.status.setText(f"{text} {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _on_models_ready(self, _: object) -> None:
        self.packs = model_packs()
        self.model.clear()
        self.model.addItems(list(self.packs))
        self._set_busy(False)
        self._done("face detection model installed")

    def check_updates(self) -> None:
        if remote.running_bundle() is None:
            QMessageBox.information(self, "Check for updates", "Running from source: update with git pull.")
            return
        job = TaskWorker(lambda progress, cancelled: remote.release())
        job.finished.connect(self._on_release_info)
        self._run(job)
        self.status.setText("Checking for updates")

    def _on_release_info(self, rel: remote.Release) -> None:
        self._set_busy(False)
        dmgs = [name for name in rel.assets if name.endswith(".dmg")]
        if rel.version <= parse_version(__version__) or not dmgs:
            self._done(f"FaceSort {__version__} is up to date")
            QMessageBox.information(self, "Check for updates", f"FaceSort {__version__} is the latest version.")
            return
        size = rel.assets[dmgs[0]].size
        answer = QMessageBox.question(
            self,
            "Update available",
            f"FaceSort {rel.tag.lstrip('v')} is available (you have {__version__}).\n\n"
            f"Download it ({size / 1e6:.0f} MB) and restart?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText(f"Update {rel.tag} skipped")
            return

        def fetch(progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> Path:
            dmg = remote.download(rel.assets[dmgs[0]], progress, cancelled)
            staged = remote.stage_update(dmg, Path(tempfile.mkdtemp(prefix="facesort-update-")))
            dmg.unlink()
            return staged

        job = TaskWorker(fetch)
        job.progress.connect(lambda done, total: self._on_bytes(done, total, f"Downloading FaceSort {rel.tag}..."))
        job.finished.connect(self._on_update_staged)
        self._run(job)

    def _on_update_staged(self, staged: Path) -> None:
        bundle = remote.running_bundle()
        if bundle is None:
            return
        log.info("installing %s over %s and restarting", staged.name, bundle)
        remote.install_and_relaunch(staged, bundle, os.getpid())
        QApplication.quit()

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
            self.status.setText("No face model yet; it downloads on launch. Reopen FaceSort to retry.")
            return
        self.settings.setValue("videos", self.videos.isChecked())
        self.settings.setValue("subfolders", self.subfolders.isChecked())
        self.settings.setValue("frames", self.frames.value())
        self.results = []
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._start_log(LOG_DIR / f"facesort-{stamp}.log")
        report = LOG_DIR / f"facesort-scan-{stamp}.csv"
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
        self.live_found = 0
        self._show_found(0)
        self._run(job)
        self.status.setText("Loading models and references")

    def cancel_job(self) -> None:
        if self.job:
            self.job.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText("Cancelling, finishing the current file")

    def _show_found(self, n: int) -> None:
        self.found.setText(f"Matches found: <b>{n}</b>")

    def _on_progress(self, done: int, total: int, score: float) -> None:
        if score >= self.threshold.value():
            self.live_found += 1
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"Scanned: {done} of {total}")
        self._show_found(self.live_found)

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
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        log.info("moving %d files to %s (journal %s)", len(matches), dest, self.journal)
        conflict = Conflict(self.conflict.currentData())
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
    w.ensure_models()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
