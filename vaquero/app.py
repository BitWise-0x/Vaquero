"""Single-window GUI: reference faces, folder to scan, destination; scan, then move."""

from __future__ import annotations

import csv
import html
import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QImage,
    QCloseEvent,
    QDesktopServices,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vaquero import __version__, remote, theme
from vaquero.remote import DEFAULT_PACK, JOURNAL, LOG_DIR, MODELS_DIR, RemoteError, parse_version
from vaquero.engine import (
    DEFAULT_VIDEO_FRAMES,
    Conflict,
    FaceMatcher,
    ScanResult,
    Transfer,
    list_media,
    pack_models,
    scan,
    transfer_matches,
)

log = logging.getLogger("vaquero")

DEFAULT_THRESHOLD = 0.35
# matches below this are lookalike territory: logged as "review" so they get a look before Execute
REVIEW_BELOW = 0.45
CLOSE_WAIT_MS = 30_000


def asset(name: str) -> Path:
    """A file from assets/: bundled into the app by build.sh, read from the repo when run from source."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "assets" / name


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
<h2>Vaquero {version}</h2>
<p>Vaquero finds every photo and video of one person in a folder and moves or copies them to another folder.
You give it a few photos of the person. It learns their face from those, checks every face in the scanned
files against it, and lists the matches. Nothing moves until you click <b>Execute</b>.</p>

<h3>Folders</h3>
<ul>
<li><span class="term">Reference faces</span>: a folder of photos of the person to find. Three to five clear photos
from different angles work best. Other people may be in them: Vaquero uses the person who appears across the photos.
If every photo shows the same two people, add one of the person alone. A photo of someone else is dropped and noted
in the log.</li>
<li><span class="term">Folder to scan</span>: where to look for the person. Photos (JPEG, HEIC, PNG, WebP, TIFF, BMP)
and videos (MOV, MP4, M4V, AVI, MKV) are checked; other files are left alone.</li>
<li><span class="term">Destination</span>: where the matches go. Created if it does not exist. It is never scanned.</li>
</ul>

<h3>Scan</h3>
<ul>
<li><span class="term">Model</span>: the face model pack. <code>buffalo_l</code> is the default.</li>
<li><span class="term">Match threshold</span>: how similar a face must be to count as a match (default {threshold:.2f}).
Raise it to drop doubtful matches, lower it to catch hard shots (small, blurred, side-on faces).
Changing it after a scan updates the matches without scanning again.</li>
<li><span class="term">Scan subfolders</span>: also look inside folders within the scan folder. Hidden folders are
skipped. Matches from subfolders all land directly in the destination.</li>
<li><span class="term">Include videos</span> and <span class="term">frames</span>: scan videos too, judging each on
that many frames spread evenly through it. More frames catch a person who appears briefly, and take longer.</li>
</ul>

<h3>Output</h3>
<ul>
<li><span class="term">Move</span> or <span class="term">Copy</span>: what Execute does with the matches. Move takes
them out of the scanned folder; Copy leaves the originals where they are.</li>
<li><span class="term">Name exists</span>: what happens when the destination already has a file with the same name.
<i>Keep both</i> adds the new file with a number (<code>IMG_1 2.png</code>).
<i>Overwrite</i> replaces the existing file, which cannot be undone.</li>
</ul>

<h3>Buttons</h3>
<ul>
<li><span class="term">Scan</span>: checks every file and lists the matches. Moves nothing.</li>
<li><span class="term">Cancel</span>: stops a scan, move or copy after the current file.</li>
<li><span class="term">Execute</span>: moves or copies the matches to the destination, per <b>Move</b> / <b>Copy</b>.</li>
<li><span class="term">Quit</span>: stops anything running after the current file and closes Vaquero. A model or
update download picks up where it stopped the next time.</li>
</ul>

<h3>Reading the results</h3>
<p>Below the progress bar, <b>Scanned</b> counts files done and <b>Matches found</b> counts matches so far.
The line ends with <b>DONE</b> or <b>CANCELLED</b>.</p>
<p>The log is split into sections: <b>Setup</b>, <b>Scanning</b>, <b>Matches</b>, <b>Result</b>, then
<b>Move</b> or <b>Copy</b>. Click a file name in the log to open the photo or video.</p>
<p>Each match shows its score and <code>refs N/M</code>: how many of the M reference photos it
matches on its own. A real match usually matches most of them (13/13); a lookalike only a few (0/13, 1/13).</p>
<p>Log colors: <span style="color:{match}">green</span> a match,
<span style="color:{warn}">orange</span> a <i>review</i> match (score below {review:.2f}, where lookalikes appear) or a warning,
<span style="color:{error}">red</span> an error, <b style="color:{done}">amber</b> section headings and a finished scan, move or copy.</p>

<h3>Files</h3>
<p>In <code>~/Library/Logs/Vaquero</code> (<b>Show logs folder</b> below, or <b>Vaquero &gt; Settings</b>):</p>
<ul>
<li><span class="term">vaquero-&lt;time&gt;.log</span>: the log of one scan and its move or copy.</li>
<li><span class="term">vaquero-scan-&lt;time&gt;.csv</span>: every scanned file with its score.</li>
</ul>
<p>In <code>~/Library/Application Support/Vaquero</code>:</p>
<ul>
<li><span class="term">vaquero-moves.csv</span>: every moved or copied file's old and new location, for undoing.</li>
<li><span class="term">models/</span>: the face model, downloaded on first launch.</li>
</ul>

<h3>Updates</h3>
<p><b>Check for updates</b> below, or <b>Vaquero &gt; Check for Updates</b>, compares this version with the latest release, then downloads it and
restarts Vaquero.
Vaquero also checks when it opens and asks only when an update exists; turn that off in <b>Vaquero &gt; Settings</b>.</p>
"""


class QtLogHandler(logging.Handler, QObject):
    record_emitted = Signal(str, bool)  # line HTML, is a section heading

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        self.record_emitted.emit(colorize(record), getattr(record, "section", False))


SLOGAN = "Find and round up every photo, by face"
READY = "Ready"
AUTO_UPDATE_KEY = "check_updates_on_launch"

# worker signals reach the window through the event loop, so every widget update runs on the main thread;
# PySide otherwise calls a Python slot directly on the emitting worker thread, and a widget repaint there crashes
QUEUED = Qt.ConnectionType.QueuedConnection

BAR_CELLS = 30
BUFFALO_URL = QUrl("vaquero:buffalo")


def progress_bar_html(label: str, done: int, total: int, detail: str) -> str:
    """A text progress bar for the log: amber filled cells, dim empty ones, the percentage and a detail."""
    frac = min(done / total, 1.0) if total else 0.0
    filled = round(frac * BAR_CELLS)
    return (
        f'<span style="color:{theme.FG_SOFT}">{html.escape(label)}</span>&nbsp;&nbsp;'
        f'<span style="color:{theme.SIGNAL}">{"█" * filled}</span>'
        f'<span style="color:{theme.LINE_STRONG}">{"█" * (BAR_CELLS - filled)}</span>&nbsp;&nbsp;'
        f'<b style="color:{theme.SIGNAL}">{frac:.0%}</b>&nbsp;&nbsp;'
        f'<span style="color:{TIME_COLOR}">{html.escape(detail)}</span>'
    )

LEVEL_COLORS = {logging.WARNING: theme.REVIEW, logging.ERROR: theme.ERROR}
TIME_COLOR = theme.DIM
MATCH_COLOR = theme.MATCH
DONE_COLOR = theme.SIGNAL


def section(title: str) -> None:
    """Start a log section: an amber heading in the log view, a ruled line in the log file."""
    log.info("%s", title, extra={"section": True})


class FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "section", False):
            return f"\n== {record.getMessage()} =="
        return super().format(record)


def _linked(msg: str, record: logging.LogRecord) -> str:
    """Escape msg and turn the record's link_text (the last occurrence) into a link that opens record.link."""
    text = html.escape(msg)
    target: Path | None = getattr(record, "link", None)
    if target is None:
        return text
    label = html.escape(getattr(record, "link_text", target.name))
    at = text.rfind(label)
    if at < 0:
        return text
    href = html.escape(QUrl.fromLocalFile(str(target)).toString(), quote=True)
    anchor = f'<a href="{href}" style="color:{theme.FG}">{label}</a>'
    return text[:at] + anchor + text[at + len(label):]


def colorize(record: logging.LogRecord) -> str:
    """One log line as HTML: dim time, level-colored text, green matches, amber headings and completions."""
    msg = record.getMessage()
    if getattr(record, "section", False):
        return f'<span style="color:{DONE_COLOR}; font-weight:700">{html.escape(msg.upper())}</span>'
    stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
    text = _linked(msg, record)
    level = html.escape(record.levelname)
    kind = msg.split(" ", 1)[0]
    if record.levelno >= logging.WARNING:
        body = f'<span style="color:{LEVEL_COLORS.get(record.levelno, LEVEL_COLORS[logging.ERROR])}">{level} {text}</span>'
    elif kind in ("match", "review"):
        color = MATCH_COLOR if kind == "match" else LEVEL_COLORS[logging.WARNING]
        body = f'<span style="color:{color}; font-weight:600">{kind}</span>{text[len(kind):]}'
    elif " done:" in msg:
        body = f'<b style="color:{DONE_COLOR}">{text}</b>'
    else:
        body = text
    return f'<span style="color:{TIME_COLOR}">{stamp}</span>&nbsp; {body}'


FILES_AND_FOLDERS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"
FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def blocked_folders(folders: list[Path]) -> list[Path]:
    """The folders macOS will not let Vaquero list. Reading one is also what makes macOS ask the user.

    A missing or offline folder is not blocked; the folder checks before a scan report it."""
    blocked = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                next(entries, None)
        except PermissionError:
            blocked.append(folder)
        except OSError:
            continue
    return blocked


def switch_guide() -> QPixmap:
    """A drawing of the row to look for in System Settings: the app's icon, its name, and the switch turned on."""
    w, h = 300, 64
    pix = QPixmap(w * 2, h * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(QColor(theme.SURFACE))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(theme.LINE_STRONG))
    p.drawRoundedRect(1, 1, w - 2, h - 2, 10, 10)
    p.drawPixmap(14, 14, QIcon(str(asset("Vaquero.png"))).pixmap(36, 36))
    font = QFont()
    font.setPixelSize(16)
    font.setWeight(QFont.Weight.DemiBold)
    p.setFont(font)
    p.setPen(QColor(theme.FG))
    p.drawText(62, 38, "Vaquero")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.SIGNAL))
    p.drawRoundedRect(w - 70, 20, 48, 26, 13, 13)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(w - 47, 22, 22, 22)
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.Normal)
    p.setFont(font)
    p.setPen(QColor(theme.SIGNAL))
    p.drawText(w - 128, 38, "turn on")
    p.end()
    return pix


def blocked_message(e: PermissionError) -> str:
    """macOS privacy controls deny a folder with EPERM; say where to allow it instead of the bare errno."""
    where = e.filename or "a folder"
    return (
        f"macOS blocked access to {where}. Allow Vaquero in System Settings > Privacy & Security > "
        "Files and Folders (Removable Volumes, Network Volumes) or Full Disk Access, then scan again."
    )


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
            paths = list_media(self.folder, self.include_videos, self.recursive, self.dest)
            total = len(paths)
            section("Scanning")
            log.info("%d files in %s", total, self.folder)
            results: list[ScanResult] = []
            with self.report.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["file", "kind", "score", "faces", "ref_agree"])
                results_iter = scan(matcher, paths, lambda: self._cancel, video_samples=self.frames)
                for r in results_iter:
                    results.append(r)
                    w.writerow(
                        [r.path.relative_to(self.folder), r.kind, f"{r.score:.3f}", r.faces, f"{r.agree}/{self.ref_count}"]
                    )
                    self.progress.emit(len(results), total, r.score)
            if self._cancel:
                log.warning("scan cancelled after %d of %d files", len(results), total)
            self.finished.emit(results, self._cancel, self.ref_count)
        except PermissionError as e:
            log.error("%s", blocked_message(e))
            self.failed.emit(blocked_message(e))
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.exception("scan failed")
            self.failed.emit(str(e))


class TransferWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(int, int, bool)  # done, failed, cancelled
    failed = Signal(str)

    def __init__(self, paths: list[Path], dest: Path, journal: Path, conflict: Conflict, mode: Transfer) -> None:
        super().__init__()
        self.paths, self.dest, self.journal, self.conflict, self.mode = paths, dest, journal, conflict, mode
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            done, failed = transfer_matches(
                self.paths, self.dest, self.journal, self.progress.emit, lambda: self._cancel, self.conflict, self.mode
            )
            self.finished.emit(done, failed, self._cancel)
        except PermissionError as e:
            log.error("%s", blocked_message(e))
            self.failed.emit(blocked_message(e))
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.exception("%s failed", self.mode.value)
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


def migrate_settings(settings: QSettings) -> None:
    """Copy the FaceSort-era settings (folders, options) into a still-empty Vaquero settings store."""
    old = QSettings("BitWise", remote.LEGACY_NAME)
    # without this, macOS lists the system-wide preferences too, and no store ever looks empty
    for store in (settings, old):
        store.setFallbacksEnabled(False)
    if settings.allKeys():
        return
    for key in old.allKeys():
        settings.setValue(key, old.value(key))
    settings.sync()  # written now, not at a clean exit that a crash or force quit would skip


class Window(QMainWindow):
    access_checked = Signal(list)  # folders macOS blocks, from the launch check's thread

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Vaquero")
        self.setWindowIcon(QIcon(str(asset("Vaquero.png"))))
        self.settings = QSettings("BitWise", "Vaquero")
        migrate_settings(self.settings)
        # the roles move every item into the macOS application menu; Qt then hides this emptied menu
        menu = self.menuBar().addMenu("Vaquero")
        for text, role, keys, slot in (
            ("About Vaquero", QAction.MenuRole.AboutRole, None, self.show_about),
            ("Check for Updates…", QAction.MenuRole.ApplicationSpecificRole, None, lambda: self.check_updates()),
            ("Settings…", QAction.MenuRole.PreferencesRole, QKeySequence.StandardKey.Preferences, self.show_settings),
            ("Quit Vaquero", QAction.MenuRole.QuitRole, QKeySequence.StandardKey.Quit, self.close),
        ):
            action = QAction(text, self)
            action.setMenuRole(role)
            if keys:
                action.setShortcut(keys)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.quiet_update_check = False
        self.results: list[ScanResult] = []
        self.thread: QThread | None = None
        self.job: ScanWorker | TransferWorker | TaskWorker | None = None
        self.journal = JOURNAL
        self.file_log: logging.FileHandler | None = None
        self.bytes_text = ""
        self.transfer_mode = Transfer.MOVE
        self.scan_root: Path | None = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(asset("Vaquero.png"))).pixmap(64, 64))
        title = QLabel(f"<span style='font-size:28pt; font-weight:700'>Vaquero</span>"
                       f"&nbsp;&nbsp;<span style='color:{TIME_COLOR}'>{__version__}</span>")
        top.addWidget(logo)
        top.addWidget(title)
        top.addStretch()
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Help: what Vaquero does and what each setting means")
        help_btn.setFixedSize(32, 32)
        help_btn.clicked.connect(self.show_help)
        top.addWidget(help_btn)
        outer.addLayout(top)
        intro = QLabel(SLOGAN)
        intro.setStyleSheet(f"color: {theme.FG_SOFT}; font-size: 14pt;")
        outer.addSpacing(4)
        outer.addWidget(intro)
        outer.addSpacing(10)
        folders = QGroupBox("Folders")
        grid = QGridLayout(folders)
        self.refs = self._folder_row(grid, 0, "Reference faces", "refs")
        self.source = self._folder_row(grid, 1, "Folder to scan", "source")
        self.dest = self._folder_row(grid, 2, "Destination", "dest")
        grid.setColumnStretch(1, 1)
        outer.addWidget(folders)

        self.packs = model_packs()
        self.model = QComboBox()
        self.model.addItems(list(self.packs))
        saved = self.settings.value("model", DEFAULT_PACK, type=str)
        if saved in self.packs:
            self.model.setCurrentText(saved)
        self.model.currentTextChanged.connect(lambda v: self.settings.setValue("model", v))
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
        scan_box = QGroupBox("Scan")
        scan_form = QFormLayout(scan_box)
        scan_form.addRow("Model", self.model)
        scan_form.addRow("Match threshold", self.threshold)
        scan_form.addRow("", self.subfolders)
        videos_row = QHBoxLayout()
        videos_row.addWidget(self.videos)
        videos_row.addWidget(self.frames)
        frames_hint = QLabel("to scan in each video")
        frames_hint.setStyleSheet(f"color: {theme.MUTED}")
        self.videos.toggled.connect(frames_hint.setEnabled)
        frames_hint.setEnabled(self.videos.isChecked())
        videos_row.addWidget(frames_hint)
        videos_row.addStretch()
        scan_form.addRow("", videos_row)

        self.transfer_move = QRadioButton("Move")
        self.transfer_copy = QRadioButton("Copy")
        self.transfer = QButtonGroup(self)
        self.transfer.addButton(self.transfer_move)
        self.transfer.addButton(self.transfer_copy)
        copying = self.settings.value("transfer", Transfer.MOVE.value, type=str) == Transfer.COPY.value
        (self.transfer_copy if copying else self.transfer_move).setChecked(True)
        self.transfer_copy.toggled.connect(
            lambda on: self.settings.setValue("transfer", (Transfer.COPY if on else Transfer.MOVE).value)
        )
        self.conflict = QComboBox()
        self.conflict.addItem("Keep both", Conflict.KEEP_BOTH.value)
        self.conflict.addItem("Overwrite", Conflict.OVERWRITE.value)
        self.conflict.setToolTip("When the destination already has a file with the same name: keep both, the new one numbered (name 2.jpg), or overwrite it")
        saved_conflict = self.conflict.findData(self.settings.value("conflict", Conflict.KEEP_BOTH.value, type=str))
        self.conflict.setCurrentIndex(max(saved_conflict, 0))
        self.conflict.currentIndexChanged.connect(lambda _: self.settings.setValue("conflict", self.conflict.currentData()))
        out_box = QGroupBox("Output")
        out_form = QFormLayout(out_box)
        transfer_row = QHBoxLayout()
        transfer_row.addWidget(self.transfer_move)
        transfer_row.addWidget(self.transfer_copy)
        transfer_row.addStretch()
        out_form.addRow("Matches", transfer_row)
        out_form.addRow("Name exists", self.conflict)
        settings_row = QHBoxLayout()
        settings_row.addWidget(scan_box, 3)
        settings_row.addWidget(out_box, 2)
        outer.addLayout(settings_row)

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.cancel_btn = QPushButton("Cancel")
        self.execute_btn = QPushButton("Execute")
        self.cancel_btn.setEnabled(False)
        self.execute_btn.setEnabled(False)
        for b in (self.scan_btn, self.cancel_btn, self.execute_btn):
            b.setMinimumHeight(38)
            buttons.addWidget(b)
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.execute_btn.clicked.connect(self.execute)
        outer.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)  # the status line below carries the counts
        outer.addWidget(self.progress)
        self.status = QLabel(READY)
        self.status.setWordWrap(True)  # a long error must not widen the window
        self.found = QLabel()
        self.found.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.found)
        outer.addLayout(status_row)
        self.live_found = 0

        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QTextBrowser()
        self.log_view.setOpenLinks(False)
        self.log_view.anchorClicked.connect(self._open_link)
        self.log_view.document().setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(300)
        log_layout.addWidget(self.log_view)
        outer.addWidget(log_box, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.close)
        self.quit_btn.setMinimumHeight(36)
        bottom.addWidget(self.quit_btn)
        outer.addLayout(bottom)

        handler = QtLogHandler()
        handler.record_emitted.connect(self._append_log, QUEUED)
        log.addHandler(handler)
        self.progress_line: QTextCursor | None = None
        self.progress_pct = -1
        self._banner()
        self.resize(960, 860)

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
            "Destination": self.dest,
        }
        paths = {}
        for name, edit in named.items():
            p = Path(edit.text().strip()).expanduser()
            if not edit.text().strip() or (name != "Destination" and not p.is_dir()):
                self.status.setText(f"{name}: not a folder")
                return None
            paths[name] = p
        if paths["Destination"].resolve() == paths["Folder to scan"].resolve():
            self.status.setText("Destination must differ from the folder being scanned")
            return None
        return paths["Reference faces"], paths["Folder to scan"], paths["Destination"]

    def _matches(self) -> list[ScanResult]:
        t = self.threshold.value()
        return [r for r in self.results if r.score >= t]

    def _update_match_count(self) -> None:
        self.settings.setValue("threshold", self.threshold.value())
        if self.results:
            m = self._matches()
            self._show_found(len(m))
            self.status.setText(f"{len(m)} matches of {len(self.results)} scanned at threshold {self.threshold.value():.2f}")
            self.execute_btn.setEnabled(bool(m))

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.execute_btn.setEnabled(not busy and bool(self._matches()))

    def _run(self, job: ScanWorker | TransferWorker | TaskWorker) -> None:
        """Run job on a fresh thread; both are released on the main thread once it ends."""
        thread = QThread()
        job.moveToThread(thread)
        thread.started.connect(job.run)
        # direct: QThread.quit is thread-safe, and a queued call would stall while closeEvent blocks in wait()
        job.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        job.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        job.failed.connect(self._on_failed, QUEUED)
        # no deleteLater: a job deleted on its own thread races the main thread deleting the QThread, and
        # PySide's disconnect hook then reads a freed wrapper (segfault). The QThread lives on the main thread,
        # so this queued lambda runs there and drops the last references after the thread has stopped.
        thread.finished.connect(lambda: self._on_thread_finished(thread, job), QUEUED)
        self.thread, self.job = thread, job
        self.progress_line = None  # each job draws its own progress line in the log
        self.progress.setValue(0)
        self._set_busy(True)
        thread.start()

    def show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Vaquero Help")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.document().setDefaultStyleSheet(
            f"h2, h3 {{ color: {theme.SIGNAL}; }} .term {{ color: {theme.SIGNAL_LIGHT}; font-weight: 600; }}"
            " li { margin-bottom: 4px; }"
        )
        text.setHtml(HELP_HTML.format(
            version=__version__, review=REVIEW_BELOW, threshold=DEFAULT_THRESHOLD,
            match=MATCH_COLOR, warn=LEVEL_COLORS[logging.WARNING], error=LEVEL_COLORS[logging.ERROR], done=DONE_COLOR,
        ))
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
        self._reveal(LOG_DIR)

    @staticmethod
    def _reveal(folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(folder)], check=False)

    def show_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        auto = QCheckBox("Check for updates when Vaquero opens")
        auto.setChecked(self.settings.value(AUTO_UPDATE_KEY, True, type=bool))
        auto.toggled.connect(lambda on: self.settings.setValue(AUTO_UPDATE_KEY, on))
        layout.addWidget(auto)
        folders = QGridLayout()
        for row, (name, folder) in enumerate((("Logs and scan reports", LOG_DIR), ("Face models", MODELS_DIR))):
            label = QLabel(f"{name}<br><span style='color:{TIME_COLOR}'>{html.escape(str(folder))}</span>")
            show = QPushButton("Show")
            show.clicked.connect(lambda _=False, f=folder: self._reveal(f))
            folders.addWidget(label, row, 0)
            folders.addWidget(show, row, 1)
        folders.setColumnStretch(0, 1)
        layout.addLayout(folders)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.resize(520, 0)
        dialog.exec()

    def ensure_models(self) -> None:
        """First launch: fetch the face model pack from the repo before anything can be scanned."""
        if self.packs:
            return
        job = TaskWorker(remote.ensure_models)
        self.bytes_text = "Downloading face detection model, please wait..."
        job.progress.connect(self._on_bytes, QUEUED)
        job.finished.connect(self._on_models_ready, QUEUED)
        self._run(job)
        self.status.setText("Downloading face detection model, please wait...")
        section("Face model")
        log.info("Downloading face detection model (%s), please wait...", DEFAULT_PACK)

    def _on_bytes(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(total // 1024, 1))
        self.progress.setValue(done // 1024)
        self.status.setText(f"{self.bytes_text} {done / 1e6:.0f} of {total / 1e6:.0f} MB")
        self._log_progress("download", done, total, f"{done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _on_models_ready(self, _: object) -> None:
        self.packs = model_packs()
        self.model.clear()
        self.model.addItems(list(self.packs))
        self._set_busy(False)
        log.info("face detection model installed, ready to scan")
        self._done("face detection model installed")

    def check_updates(self, quiet: bool = False) -> None:
        """Look for a newer release. quiet (the launch check) only speaks up when an update exists."""
        if self.job:
            if not quiet:
                QMessageBox.information(self, "Check for updates", "Wait for the current task to finish.")
            return
        if remote.running_bundle() is None:
            if not quiet:
                QMessageBox.information(self, "Check for updates", "Running from source: update with git pull.")
            return

        def latest(progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> remote.Release | None:
            if not quiet:
                return remote.release()
            try:
                return remote.release()
            except (RemoteError, OSError) as e:
                log.info("update check skipped: %s", e)
                return None

        self.quiet_update_check = quiet
        job = TaskWorker(latest)
        job.finished.connect(self._on_release_info, QUEUED)
        self._run(job)
        self.status.setText("Checking for updates")

    def _on_release_info(self, rel: remote.Release | None) -> None:
        self._set_busy(False)
        quiet = self.quiet_update_check
        dmgs = [name for name in rel.assets if name.endswith(".dmg")] if rel else []
        if rel is None or rel.version <= parse_version(__version__) or not dmgs:
            if quiet:
                self.status.setText(READY)
                return
            self._done(f"Vaquero {__version__} is up to date")
            QMessageBox.information(self, "Check for updates", f"Vaquero {__version__} is the latest version.")
            return
        size = rel.assets[dmgs[0]].size
        answer = QMessageBox.question(
            self,
            "Update available",
            f"Vaquero {rel.tag.lstrip('v')} is available (you have {__version__}).\n\n"
            f"Download it ({size / 1e6:.0f} MB) and restart?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText(f"Update {rel.tag} skipped")
            return

        def fetch(progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> Path:
            dmg = remote.download(rel.assets[dmgs[0]], progress, cancelled)
            staged = remote.stage_update(dmg, Path(tempfile.mkdtemp(prefix="vaquero-update-")))
            dmg.unlink()
            return staged

        job = TaskWorker(fetch)
        self.bytes_text = f"Downloading Vaquero {rel.tag}..."
        job.progress.connect(self._on_bytes, QUEUED)
        job.finished.connect(self._on_update_staged, QUEUED)
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
            "About Vaquero",
            f"<h3 style='color:{theme.SIGNAL}'>Vaquero {__version__}</h3>"
            f"<p>{SLOGAN}</p>"
            "<p><b>Fully private.</b> Runs on your own Mac; no photo leaves it.</p>"
            "<p>Detection: SCRFD · Recognition: ArcFace (InsightFace model packs)<br>"
            "Inference: ONNX Runtime with Core ML on Apple Silicon</p>"
            "<p>InsightFace pretrained models are licensed for non-commercial research use only.</p>"
            '<p><a href="https://github.com/BitWise-0x/Vaquero">github.com/BitWise-0x/Vaquero</a></p>',
        )

    def _on_thread_finished(self, thread: QThread, job: QObject) -> None:
        thread.wait()
        thread.finished.disconnect()  # the connection's lambda holds the only other references
        # a job started from the old one's finished handler may already own self.thread
        if self.thread is thread:
            self.thread = self.job = None

    def check_access_at_launch(self) -> None:
        """Touch the saved folders off the main thread: macOS asks now, and a refusal gets the access dialog.

        Off the main thread because an unreachable network share can stall a directory read."""
        folders = [Path(e.text().strip()).expanduser() for e in (self.refs, self.source, self.dest) if e.text().strip()]
        if not folders:
            return
        self.access_checked.connect(self._on_access_checked, QUEUED)
        threading.Thread(target=lambda: self.access_checked.emit(blocked_folders(folders)), daemon=True).start()

    def _on_access_checked(self, blocked: list[Path]) -> None:
        if blocked:
            self.ask_for_access(blocked)

    def ask_for_access(self, blocked: list[Path]) -> bool:
        """Walk the user through allowing access; True once every blocked folder opens."""
        while blocked:
            box = QMessageBox(self)
            box.setWindowTitle("Allow access")
            box.setIconPixmap(switch_guide())
            box.setText("Vaquero needs your permission to open:\n" + "\n".join(str(b) for b in blocked))
            box.setInformativeText(
                "1. Click Open Settings.\n"
                "2. Find Vaquero and turn its switch on, like the picture.\n"
                "3. Come back and click Try Again.\n\n"
                "Vaquero not in the list? Click Full Disk Access instead: drag Vaquero from the Finder window "
                "into the list, turn it on, then click Try Again."
            )
            settings = box.addButton("Open Settings", QMessageBox.ButtonRole.ActionRole)
            full_disk = box.addButton("Full Disk Access", QMessageBox.ButtonRole.ActionRole)
            retry = box.addButton("Try Again", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(settings)
            box.exec()
            clicked = box.clickedButton()
            if clicked is settings:
                QDesktopServices.openUrl(QUrl(FILES_AND_FOLDERS_URL))
            elif clicked is full_disk:
                QDesktopServices.openUrl(QUrl(FULL_DISK_ACCESS_URL))
                # Full Disk Access only lists apps someone added: show the app in Finder, ready to drag in
                if (bundle := remote.running_bundle()) is not None:
                    subprocess.run(["open", "-R", str(bundle)], check=False)
            elif clicked is not retry:
                self.status.setText("Waiting for access to " + ", ".join(b.name or str(b) for b in blocked))
                return False
            blocked = blocked_folders(blocked)
        self.status.setText(READY)
        return True

    def start_scan(self) -> None:
        dirs = self._dirs()
        if dirs is None:
            return
        refs, source, dest = dirs
        if not self.ask_for_access(blocked_folders([refs, source, dest])):
            return
        pack = self.packs.get(self.model.currentText())
        if pack is None:
            self.status.setText("No face model yet; it downloads on launch. Reopen Vaquero to retry.")
            return
        self.settings.setValue("videos", self.videos.isChecked())
        self.settings.setValue("subfolders", self.subfolders.isChecked())
        self.settings.setValue("frames", self.frames.value())
        self.results = []
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._start_log(LOG_DIR / f"vaquero-{stamp}.log")
        report = LOG_DIR / f"vaquero-scan-{stamp}.csv"
        log.info("scan report: %s", report, extra={"link": report, "link_text": str(report)})
        log.info(
            "model %s, threshold %.2f, subfolders %s, videos %s",
            self.model.currentText(),
            self.threshold.value(),
            "on" if self.subfolders.isChecked() else "off",
            f"{self.frames.value()} frames each" if self.videos.isChecked() else "off",
        )
        self.scan_root = source
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
        job.progress.connect(self._on_progress, QUEUED)
        job.finished.connect(self._on_finished, QUEUED)
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
        self._log_progress("scan", done, total, f"{done} of {total} files")
        self._show_found(self.live_found)

    def _start_log(self, path: Path) -> None:
        """Each scan, and the move that follows it, gets its own log file and a cleared log view."""
        if self.file_log:
            log.removeHandler(self.file_log)
            self.file_log.close()
        self.file_log = logging.FileHandler(path, encoding="utf-8")
        self.file_log.setFormatter(FileFormatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(self.file_log)
        self.log_view.clear()
        self.progress_line = None
        self._banner()
        section("Setup")
        log.info("Vaquero %s, log file: %s", __version__, path, extra={"link": path, "link_text": str(path)})

    def _done(self, text: str, cancelled: bool = False) -> None:
        self.status.setText(f"<b>{'CANCELLED' if cancelled else 'DONE'}</b>: {text}")

    def _on_finished(self, results: list[ScanResult], cancelled: bool, refs: int) -> None:
        self.results = results
        self._set_busy(False)
        self._update_match_count()
        matches = self._matches()
        review = sum(r.score < REVIEW_BELOW for r in matches)
        section(f"Matches ({len(matches)})")
        for r in sorted(matches, key=lambda r: -r.score):
            kind = "review" if r.score < REVIEW_BELOW else "match"
            shown = str(r.path.relative_to(self.scan_root)) if self.scan_root and r.path.is_relative_to(self.scan_root) else str(r.path)
            log.info(
                "%s %.3f · refs %d/%d · %s", kind, r.score, r.agree, refs, shown, extra={"link": r.path, "link_text": shown}
            )
        section("Result")
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

    def execute(self) -> None:
        dirs = self._dirs()
        matches = self._matches()
        if dirs is None or not matches:
            return
        dest = dirs[2]
        mode = Transfer.COPY if self.transfer_copy.isChecked() else Transfer.MOVE
        conflict = Conflict(self.conflict.currentData())
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        section(mode.value.capitalize())
        log.info("%sing %d files to %s", mode.value, len(matches), dest, extra={"link": dest, "link_text": str(dest)})
        log.info(
            "name conflicts: %s, journal %s",
            "keep both" if conflict is Conflict.KEEP_BOTH else "overwrite",
            self.journal,
            extra={"link": self.journal, "link_text": str(self.journal)},
        )
        job = TransferWorker([r.path for r in matches], dest, self.journal, conflict, mode)
        self.transfer_mode = mode
        job.progress.connect(self._on_transfer_progress, QUEUED)
        job.finished.connect(self._on_transfer_finished, QUEUED)
        self._run(job)

    def _on_transfer_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.status.setText(f"{self._transfer_verb().capitalize()} {done} of {total}")
        self._log_progress(self.transfer_mode.value, done, total, f"{done} of {total} files")

    def _on_transfer_finished(self, done: int, failed: int, cancelled: bool) -> None:
        verb = self._transfer_verb()
        log.info("%s done: %d %s, %d failed%s", self.transfer_mode.value, done, verb, failed, " (cancelled)" if cancelled else "")
        self.results = [r for r in self.results if r.path.exists()]
        self._set_busy(False)
        self._update_match_count()
        self._done(f"{verb} {done}, {failed} failed. Undo list in {self.journal.name}", cancelled)

    def _new_block(self, top: int = 0, bottom: int = 0) -> QTextCursor:
        """A cursor in a fresh block at the end of the log, with the given spacing."""
        doc = self.log_view.document()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = QTextBlockFormat()
        block.setTopMargin(top if not doc.isEmpty() else 0)
        block.setBottomMargin(bottom)
        if doc.isEmpty():
            cursor.setBlockFormat(block)
        else:
            # a fresh char format, so a link at the end of the last line does not carry over
            cursor.insertBlock(block, QTextCharFormat())
        return cursor

    def _keep_scrolled(self, write: Callable[[], None]) -> None:
        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        write()
        if at_bottom:
            bar.setValue(bar.maximum())

    def _append_log(self, line: str, heading: bool) -> None:
        """Add one log line as its own block; headings get space above them."""
        self._keep_scrolled(lambda: self._new_block(10 if heading else 0, 2 if heading else 0).insertHtml(line))

    def _log_progress(self, label: str, done: int, total: int, detail: str) -> None:
        """Draw the job's progress line in the log, then redraw it in place as the percentage moves."""
        pct = done * 100 // total if total else 0
        if self.progress_line is not None and pct == self.progress_pct and done != total:
            return
        self.progress_pct = pct
        line = progress_bar_html(label, done, total, detail)

        def write() -> None:
            if self.progress_line is None:
                self.progress_line = self._new_block(4, 4)
                self.progress_line.insertHtml(line)
                return
            # the cursor follows its block as later lines are added below it
            self.progress_line.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            self.progress_line.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            self.progress_line.insertHtml(line)

        self._keep_scrolled(write)

    def _banner(self) -> None:
        """The buffalo_l mascot, name, version and engine at the top of the log."""
        doc = self.log_view.document()
        doc.addResource(QTextDocument.ResourceType.ImageResource, BUFFALO_URL, QImage(str(asset("buffalo.png"))))
        pack = self.model.currentText() or DEFAULT_PACK
        banner = (
            '<table cellspacing="0" cellpadding="0"><tr>'
            f'<td valign="middle"><img src="{BUFFALO_URL.toString()}" width="44" height="44"></td>'
            '<td valign="middle" style="padding-left:12px">'
            f'<span style="color:{theme.SIGNAL}; font-size:15pt; font-weight:700">Vaquero</span>'
            f'&nbsp;&nbsp;<span style="color:{TIME_COLOR}">{__version__}</span><br>'
            f'<span style="color:{theme.MUTED}">{html.escape(pack)} · SCRFD detection · ArcFace recognition · Core ML</span>'
            "</td></tr></table>"
        )
        self._new_block(0, 6).insertHtml(banner)

    def _transfer_verb(self) -> str:
        return "moved" if self.transfer_mode is Transfer.MOVE else "copied"

    def _open_link(self, url: QUrl) -> None:
        path = Path(url.toLocalFile())
        if not path.exists():
            self.status.setText(f"No longer there: {path}")
            return
        QDesktopServices.openUrl(url)

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
    remote.migrate_legacy()
    app = QApplication(sys.argv)
    theme.apply(app, asset("check.svg").as_posix())
    w = Window()
    w.show()
    w.ensure_models()
    w.check_access_at_launch()
    if w.settings.value(AUTO_UPDATE_KEY, True, type=bool):
        w.check_updates(quiet=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
