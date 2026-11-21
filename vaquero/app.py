"""Single-window GUI: reference faces, folder to scan, destination; scan, then move."""

from __future__ import annotations

import csv
import errno
import html
import logging
import math
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QLockFile,
    QMessageLogContext,
    QObject,
    QRectF,
    QSettings,
    Qt,
    QtMsgType,
    QThread,
    QTimer,
    QEvent,
    QUrl,
    Signal,
    SignalInstance,
    qInstallMessageHandler,
)
from PySide6.QtGui import (
    QAction,
    QImage,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QColor,
    QContextMenuEvent,
    QCursor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextBrowser,
    QToolTip,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vaquero import __version__, access, logs, macos, remote, theme
from vaquero.logs import FileFormatter
from vaquero.menubar import StatusItem
from vaquero.settings import (
    AUTO_UPDATE_KEY,
    KEEP_SCANS_KEY,
    MENU_BAR_HINT_KEY,
    NOTIFY_KEY,
    SHOW_IN_DOCK_KEY,
    SHOW_IN_MENU_BAR_KEY,
    SettingsDialog,
)
from vaquero.remote import DEFAULT_PACK, JOURNAL, LOG_DIR, MODELS_DIR, RemoteError, parse_version
from vaquero.engine import (
    DEFAULT_VIDEO_FRAMES,
    Conflict,
    FaceMatcher,
    JournalRow,
    ScanResult,
    SourceGone,
    Transfer,
    TransferCounts,
    TransferStopped,
    last_run,
    list_media,
    pack_models,
    scan,
    transfer_matches,
    undo_run,
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
<i>Overwrite</i> replaces the existing file and sends it to the Trash; on a network share without a Trash it is
replaced outright.</li>
</ul>

<h3>Buttons</h3>
<ul>
<li><span class="term">Scan</span>: checks every file and lists the matches. Moves nothing.</li>
<li><span class="term">Cancel</span>: stops a scan, move or copy after the current file, once you confirm.</li>
<li><span class="term">Execute</span>: moves or copies the matches to the destination, per <b>Move</b> / <b>Copy</b>.</li>
<li><span class="term">Quit</span>: closes Vaquero. With a scan, move, copy or download running, it asks first, then stops
it after the current file. A model or update download picks up where it stopped the next time.</li>
</ul>

<h3>Undo</h3>
<p><b>Edit &gt; Undo</b> (<b>⌘Z</b>) reverses the last copy or move: moved files go back to where they were, copies go to
the Trash, and files an overwrite replaced come back out of the Trash. Anything changed since then is left alone.
Undo again for the copy or move before it.</p>

<h3>Menu bar and Dock</h3>
<p>Vaquero shows in the Dock, the menu bar, or both (<b>Vaquero &gt; Settings</b>). With the menu bar icon, closing the
window keeps Vaquero running there: the icon sweeps while a scan, copy, move or download runs, and its menu shows
the progress, Cancel, Scan, Undo and Quit. <b>Quit</b> or <b>⌘Q</b> quits.</p>

<h3>Shortcuts</h3>
<ul>
<li><b>⌘R</b> Scan, <b>⌘E</b> Execute, <b>⌘.</b> Cancel, <b>⌘Z</b> Undo, <b>⌘,</b> Settings, <b>⌘W</b> close the window.</li>
</ul>

<h3>Reading the results</h3>
<p>Above the log, <b>Scanned</b> counts files done and <b>Matches found</b> counts matches so far, in amber.
The line ends with <b>DONE</b> or <b>CANCELLED</b>.</p>
<p>The log is split into sections: <b>Setup</b>, <b>Scanning</b>, <b>Matches</b>, <b>Result</b>, then
<b>Move</b> or <b>Copy</b>. Click a file or folder name in the log to open it, Option-click to show it in the
Finder, or right-click it to copy its path. Hover over a name for its full path.</p>
<p>Each match shows its score and <code>refs N/M</code>: how many of the M reference photos it
matches on its own. A real match usually matches most of them (13/13); a lookalike only a few (0/13, 1/13).</p>
<p>Log colors: <span style="color:{match}">green</span> a match,
<span style="color:{warn}">orange</span> a <i>review</i> match (score below {review:.2f}, where lookalikes appear) or a warning,
<span style="color:{error}">red</span> an error, <b style="color:{done}">amber</b> section headings and a finished scan, move or copy.</p>

<h3>Files</h3>
<p>In <code>~/Library/Logs/Vaquero</code> (<b>Show logs folder</b> below, or <b>Vaquero &gt; Settings</b>):</p>
<ul>
<li><span class="term">vaquero.log</span>: everything since launch, with the details of any error (<b>Help &gt; Open Log</b>).</li>
<li><span class="term">vaquero-&lt;time&gt;.log</span>: the log of one scan and its move or copy.</li>
<li><span class="term">vaquero-scan-&lt;time&gt;.csv</span>: every scanned file with its score.</li>
</ul>
<p>In <code>~/Library/Application Support/Vaquero</code>:</p>
<ul>
<li><span class="term">vaquero-moves.csv</span>: every moved or copied file's old and new location, for <b>Undo</b>.</li>
<li><span class="term">models/</span>: the face model, downloaded on first launch.</li>
</ul>

<h3>Updates</h3>
<p><b>Check for updates</b> below, or <b>Vaquero &gt; Check for Updates</b>, compares this version with the latest release, then downloads it and
restarts Vaquero.
Vaquero also checks when it opens and asks only when an update exists; turn that off in <b>Vaquero &gt; Settings</b>.</p>
"""


class LogView(QTextBrowser):
    """The log, whose file and folder names are links: right-click one to open it, show it in the Finder or copy
    its path."""

    reveal_requested = Signal(QUrl)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 (Qt override)
        href = self.anchorAt(event.pos())
        menu = self.createStandardContextMenu(event.pos())
        if href:
            url = QUrl(href)
            first = menu.actions()[0] if menu.actions() else None
            items = [
                ("Open", lambda: self.anchorClicked.emit(url)),
                ("Show in Finder", lambda: self.reveal_requested.emit(url)),
                ("Copy Path", lambda: QApplication.clipboard().setText(url.toLocalFile())),
            ]
            for text, slot in items:
                action = QAction(text, menu)
                action.triggered.connect(slot)
                menu.insertAction(first, action)
            menu.insertSeparator(first)
        menu.exec(event.globalPos())


class QtLogHandler(logging.Handler, QObject):
    record_emitted = Signal(str, bool)  # line HTML, is a section heading

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        if not getattr(record, "file_only", False):
            self.record_emitted.emit(colorize(record), getattr(record, "section", False))


SLOGAN = "Find and Round up Photos and Videos by Face"
ISSUES_URL = "https://github.com/BitWise-0x/Vaquero/issues/new"
SITE_URL = "https://vaquero.bitwise0x.com"
READY = "Ready"

# worker signals reach the window through the event loop, so every widget update runs on the main thread;
# PySide otherwise calls a Python slot directly on the emitting worker thread, and a widget repaint there crashes
QUEUED = Qt.ConnectionType.QueuedConnection

BAR_CELLS = 30
BUFFALO_URL = QUrl("vaquero:buffalo")


def cocoa() -> bool:
    """Running with the real macOS window system (tests run offscreen, without a Dock or menu bar)."""
    return QGuiApplication.platformName() == "cocoa"


def time_left(elapsed: float, done: int, total: int) -> str:
    """ " · about 3 min left", once a job has run long enough for its pace to mean something."""
    if done <= 0 or done >= total or elapsed < 5:
        return ""
    left = elapsed / done * (total - done)
    if left < 60:
        return " · under a minute left"
    if left < 3600:
        return f" · about {round(left / 60)} min left"
    return f" · about {left / 3600:.1f} h left"


def status_value(text: str) -> str:
    """A count or summary in the status line above the log, in amber."""
    return f'<b style="color:{theme.SIGNAL}">{html.escape(text)}</b>'


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


FILES_AND_FOLDERS_URL = access.PRIVACY_URL
FULL_DISK_ACCESS_URL = access.FULL_DISK_ACCESS_URL
DONT_OFFER_MOVE_KEY = "dont_offer_move"


def blocked_folders(folders: list[Path]) -> list[Path]:
    """The folders macOS will not let Vaquero list. Reading one is also what makes macOS ask the user.

    A missing or offline folder is not blocked; the folder checks before a scan report it."""
    blocked = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                next(entries, None)
        except PermissionError as e:
            if e.errno == errno.EPERM:  # privacy controls; EACCES is plain file permissions, which Settings cannot fix
                blocked.append(folder)
        except OSError:
            continue
    return blocked


FIELDS = ("Reference faces", "Folder to scan", "Destination")
PREFLIGHT_TIMEOUT_MS = 20_000


def folder_problem(named: dict[str, Path], current: list[str]) -> tuple[str, list[Path]]:
    """What stops a scan or a move with these folders ("" when nothing does), and the ones macOS blocks.

    Runs on a thread: a dead network share can stall a stat for a minute. current[0] names the folder being looked
    at, for the message when the check does not come back."""
    for name, p in named.items():
        current[0] = name
        if drive := logs.missing_volume(p):
            return f"{name}: the drive {drive} is not connected. Connect it and try again.", []
        if name == "Destination":
            if p.exists() and not p.is_dir():
                return f"{name}: not a folder", []
            if p.is_dir() and not os.access(p, os.W_OK | os.X_OK):
                return f"{name}: no permission to add files. Check its Sharing & Permissions in Finder > Get Info.", []
        elif not p.is_dir():
            return f"{name}: not a folder", []
        elif not os.access(p, os.R_OK | os.X_OK):
            return f"{name}: no permission to read it. Check its Sharing & Permissions in Finder > Get Info.", []
    if os.path.realpath(named["Destination"]) == os.path.realpath(named["Folder to scan"]):
        return "Destination must differ from the folder being scanned", []
    current[0] = ""
    return "", blocked_folders([p for p in named.values() if p.is_dir()])


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


class ScanIcon(QLabel):
    """The app icon with the amber scan line sweeping down inside its viewfinder corners, as on the website and in
    the README (scripts/make_scanline.py renders that file with the same recipe)."""

    PERIOD_MS, SWEEP_MS, FRAME_MS = 4000, 2400, 33
    INSET = 0.14  # the line stays inside the viewfinder corners
    CORE, HALO = 3 / 480, 26 / 480  # gaussian widths of the sharp core and the soft halo, per icon edge
    SPILL = 40 / 480  # how far the glow may reach past the sweep's ends
    STOPS = 61

    def __init__(self, size: int) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.icon = QIcon(str(asset("Vaquero.png"))).pixmap(size, size)
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(self.FRAME_MS)

    def stop(self) -> None:
        """Hold the plain icon."""
        self.timer.stop()
        self.update()

    def paintEvent(self, _: object) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self.icon)
        t = (time.monotonic() - self.started) * 1000 % self.PERIOD_MS
        if not self.timer.isActive() or t >= self.SWEEP_MS:
            return
        progress = t / self.SWEEP_MS
        edge = float(self.width())
        lo, hi = edge * self.INSET, edge * (1 - self.INSET)
        y = lo + progress * (hi - lo)
        fade = math.sin(math.pi * progress) ** 0.6
        reach = 3 * edge * self.HALO
        grad = QLinearGradient(0, y - reach, 0, y + reach)
        for i in range(self.STOPS):
            d = (2 * i / (self.STOPS - 1) - 1) * reach
            a = 0.85 * math.exp(-((d / (edge * self.CORE)) ** 2)) + 0.22 * math.exp(-((d / (edge * self.HALO)) ** 2))
            color = QColor(theme.SIGNAL)
            # painting the accent over the icon at this opacity blends it toward the accent, as the recipe does
            color.setAlphaF(min(1.0, a * fade * 0.9))
            grad.setColorAt(i / (self.STOPS - 1), color)
        top, bottom = max(y - reach, lo - edge * self.SPILL), min(y + reach, hi + edge * self.SPILL)
        p.fillRect(QRectF(lo, top, hi - lo, bottom - top), grad)


class Job(QObject):
    """A worker that Window._run moves onto its own thread."""

    def __init__(self) -> None:
        super().__init__()
        self.ended = False

    def _emit_end(self, signal: SignalInstance, *args: object) -> None:
        # set before the emit: the window's handler for it may start the next job, and must see this one as over
        self.ended = True
        signal.emit(*args)


class ScanWorker(Job):
    progress = Signal(int, int, float)  # done, total, score of the file just scanned
    finished = Signal(list, bool, int, str)  # list[ScanResult], cancelled, reference photos kept, why it stopped early
    listing = Signal(int)  # media files found so far, while the folder is listed
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
        self.results: list[ScanResult] = []  # filled as the scan runs, so an early stop still hands back what it found

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            matcher = FaceMatcher(self.model_dir)
            matcher.load_references(self.refs)
            self.ref_count = matcher.ref_count
            if self._cancel:
                log.warning("scan cancelled before scanning")
                self._emit_end(self.finished, [], True, self.ref_count, "")
                return
            paths = list_media(
                self.folder, self.include_videos, self.recursive, self.dest, lambda: self._cancel, self.listing.emit
            )
            if self._cancel:
                log.warning("scan cancelled while listing files")
                self._emit_end(self.finished, [], True, self.ref_count, "")
                return
            total = len(paths)
            section("Scanning")
            log.info("%d files in %s", total, self.folder, extra={"link": self.folder, "link_text": str(self.folder)})
            results = self.results
            with self.report.open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["file", "kind", "score", "faces", "ref_agree"])
                results_iter = scan(
                    matcher, paths, lambda: self._cancel, video_samples=self.frames, gone=lambda: not self.folder.is_dir()
                )
                for r in results_iter:
                    results.append(r)
                    w.writerow(
                        [r.path.relative_to(self.folder), r.kind, f"{r.score:.3f}", r.faces, f"{r.agree}/{self.ref_count}"]
                    )
                    self.progress.emit(len(results), total, r.score)
            if self._cancel:
                log.warning("scan cancelled after %d of %d files", len(results), total)
            self._emit_end(self.finished, results, self._cancel, self.ref_count, "")
        except SourceGone as e:
            self._emit_end(self.finished, self.results, False, self.ref_count, str(e))
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.error("scan stopped: %s", logs.describe(e), exc_info=True)
            if self.results:  # what was scanned so far stays usable
                self._emit_end(self.finished, self.results, False, self.ref_count, logs.describe(e))
            else:
                self._emit_end(self.failed, logs.describe(e))


class TransferWorker(Job):
    progress = Signal(int, int)
    finished = Signal(object, bool, str)  # TransferCounts, cancelled, why it stopped early ("" when it did not)
    failed = Signal(str)

    def __init__(self, paths: list[Path], dest: Path, journal: Path, conflict: Conflict, mode: Transfer) -> None:
        super().__init__()
        self.paths, self.dest, self.journal, self.conflict, self.mode = paths, dest, journal, conflict, mode
        self._cancel = False
        self.moved: set[Path] = set()  # sources no longer where the scan found them

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            counts = transfer_matches(
                self.paths, self.dest, self.journal, self.progress.emit, lambda: self._cancel, self.conflict, self.mode,
                self.moved.add,
            )
            self._emit_end(self.finished, counts, self._cancel, "")
        except TransferStopped as e:
            self._emit_end(self.finished, e.counts, False, str(e))
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.error("%s stopped: %s", self.mode.value, logs.describe(e), exc_info=True)
            self._emit_end(self.failed, logs.describe(e))


class UndoWorker(Job):
    progress = Signal(int, int)
    finished = Signal(object, bool)  # TransferCounts, cancelled
    failed = Signal(str)

    def __init__(self, rows: list[JournalRow], journal: Path) -> None:
        super().__init__()
        self.rows, self.journal = rows, journal
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            counts = undo_run(self.rows, self.journal, self.progress.emit, lambda: self._cancel)
            self._emit_end(self.finished, counts, self._cancel)
        except Exception as e:  # surfaced in the UI and log, the worker thread must not die silently
            log.error("undo stopped: %s", logs.describe(e), exc_info=True)
            self._emit_end(self.failed, logs.describe(e))


class TaskWorker(Job):
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
            self._emit_end(self.finished, self.fn(self.progress.emit, lambda: self._cancel))
        except Exception as e:  # anything left uncaught would keep the thread running and the window busy for good
            # a stopped download raises too, and is not an error
            if self._cancel:
                log.warning("%s", e)
            else:
                log.error("%s", logs.describe(e), exc_info=not isinstance(e, RemoteError))
            self._emit_end(self.failed, str(e) if self._cancel else logs.describe(e))


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
    preflight_done = Signal(object, object)  # token, ((problem, blocked folders), folders): the scan/move check
    access_checked = Signal(list, list)  # folders macOS blocks, (field, drive) pairs not connected; launch check

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Vaquero")
        self.setWindowIcon(QIcon(str(asset("Vaquero.png"))))
        self.settings = QSettings("BitWise", "Vaquero")
        migrate_settings(self.settings)
        self.quiet_update_check = False
        self.quitting = False  # a real quit, not the window closing into the menu bar
        self.progress_state: tuple[str, int, int, str] | None = None  # label, done, total, detail of the running job
        self.job_started = 0.0
        self.dropped: list[ScanResult] = []  # results a move took away; an undo brings them back
        self._undo_cache: tuple[tuple[float, int], list[JournalRow]] | None = None
        self.results: list[ScanResult] = []
        self.thread: QThread | None = None
        self.job: Job | None = None
        self.task: str | None = None  # the running job as a confirmation names it; None needs no confirmation
        self.stopping = False  # a cancel or quit asked the running job to stop
        self.journal = JOURNAL
        self.pending_check: object | None = None  # the folder check in flight before a scan or move
        self.preflight_then: Callable[[Path, Path, Path], None] = lambda *folders: None
        self.preflight_current: list[str] = [""]
        self.retry: Callable[[], None] | None = None  # what Try again reruns after a failure
        self.after_job: Callable[[], None] | None = None  # runs once the current job's thread is released
        self.activity: macos.Activity | None = None  # keeps the Mac awake while a user-started job runs
        self.file_log: logging.FileHandler | None = None
        self.bytes_text = ""
        self.transfer_mode = Transfer.MOVE
        self.scan_root: Path | None = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        self.logo = ScanIcon(64)
        title = QLabel(f"<span style='font-size:28pt; font-weight:700'>Vaquero</span>"
                       f"&nbsp;&nbsp;<span style='color:{TIME_COLOR}'>{__version__}</span>")
        # the slogan sits under the name, beside the logo
        intro = QLabel(SLOGAN)
        intro.setStyleSheet(f"color: {theme.FG_SOFT}; font-size: 12pt;")
        lockup = QVBoxLayout()
        lockup.setSpacing(0)
        lockup.addStretch()
        lockup.addWidget(title)
        lockup.addWidget(intro)
        lockup.addStretch()
        top.addWidget(self.logo)
        top.addLayout(lockup)
        top.addStretch()
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Help: what Vaquero does and what each setting means")
        help_btn.setFixedSize(32, 32)
        help_btn.clicked.connect(self.show_help)
        top.addWidget(help_btn)
        outer.addLayout(top)
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
        self.subfolders.toggled.connect(lambda on: self.settings.setValue("subfolders", on))
        self.videos = QCheckBox("Include videos")
        self.videos.setChecked(self.settings.value("videos", True, type=bool))
        self.videos.toggled.connect(lambda on: self.settings.setValue("videos", on))
        self.frames = QSpinBox()
        self.frames.setRange(1, 60)
        self.frames.setValue(self.settings.value("frames", DEFAULT_VIDEO_FRAMES, type=int))
        self.frames.valueChanged.connect(lambda n: self.settings.setValue("frames", n))
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

        self.status = QLabel(READY)
        self.status.setWordWrap(True)  # a long error must not widen the window
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.status.linkActivated.connect(self._on_status_link)
        self.preflight_done.connect(self._on_preflight_done, QUEUED)
        self.found = QLabel()
        self.found.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 10, 4, 6)  # room between the buttons above and the log below
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.found)
        outer.addLayout(status_row)
        self.live_found = 0

        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = LogView()
        self.log_view.setOpenLinks(False)
        self.log_view.anchorClicked.connect(self._open_link)
        self.log_view.reveal_requested.connect(lambda url: self._open_link(url, reveal=True))
        self.log_view.highlighted.connect(self._hover_link)
        self.log_view.document().setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(300)
        log_layout.addWidget(self.log_view)
        outer.addWidget(log_box, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 8, 0, 0)  # room between the log and Quit
        bottom.addStretch()
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.quit_app)
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
        if geometry := self.settings.value("geometry"):
            self.restoreGeometry(geometry)
        self.status_item = StatusItem(self, asset)
        self._build_menus()

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

    def _preflight(self, then: Callable[[Path, Path, Path], None]) -> None:
        """Check the folders on a thread, then call then(refs, source, dest) on the main thread. The window stays
        responsive while a share is slow, and a check that does not come back is reported instead of waited on."""
        named = {name: edit.text().strip() for name, edit in zip(FIELDS, (self.refs, self.source, self.dest), strict=True)}
        if empty := next((name for name, text in named.items() if not text), None):
            self.status.setText(f"{empty}: pick a folder")
            return
        folders = {name: Path(text).expanduser() for name, text in named.items()}
        token, current = object(), [""]
        self.pending_check, self.preflight_then, self.preflight_current = token, then, current
        self.scan_btn.setEnabled(False)
        self.execute_btn.setEnabled(False)
        self.status.setText("Checking folders")
        threading.Thread(
            target=lambda: self.preflight_done.emit(token, (folder_problem(folders, current), folders)), daemon=True
        ).start()
        QTimer.singleShot(PREFLIGHT_TIMEOUT_MS, lambda: self._on_preflight_timeout(token))

    def _on_preflight_done(self, token: object, result: tuple[tuple[str, list[Path]], dict[str, Path]]) -> None:
        if token is not self.pending_check:  # timed out already, or superseded
            return
        self.pending_check = None
        (problem, blocked), folders = result
        self._set_busy(False)
        if problem:
            self.status.setText(html.escape(problem))
            return
        if blocked and not self.ask_for_access(blocked):
            return
        self.preflight_then(*(folders[name] for name in FIELDS))

    def _on_preflight_timeout(self, token: object) -> None:
        if token is not self.pending_check:
            return
        self.pending_check = None
        self._set_busy(False)
        where = self.preflight_current[0] or "A folder"
        log.warning("%s did not respond within %d s", where, PREFLIGHT_TIMEOUT_MS // 1000)
        self.status.setText(f"{html.escape(where)} is not responding. Check the drive or network share, then try again.")

    def _matches(self) -> list[ScanResult]:
        t = self.threshold.value()
        return [r for r in self.results if r.score >= t]

    def _update_match_count(self) -> None:
        self.settings.setValue("threshold", self.threshold.value())
        if self.results:
            m = self._matches()
            self._show_found(len(m))
            self.status.setText(f"{len(m)} matches of {len(self.results)} scanned at threshold {self.threshold.value():.2f}")
            self.execute_btn.setEnabled(bool(m) and not self.busy)

    @property
    def busy(self) -> bool:
        """A job is running and has not yet reported its end (its thread may still be winding down)."""
        return self.job is not None and not self.job.ended

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy and bool(self.packs))  # no model yet: the download failed or was cancelled
        self.cancel_btn.setEnabled(busy)
        self.execute_btn.setEnabled(not busy and bool(self._matches()))
        if not busy:
            self.progress_state = None
        # a quiet background check is not work the user is waiting on
        self.status_item.set_working(busy and self.task is not None)

    def _run(self, job: Job, task: str | None = None, retry: Callable[[], None] | None = None) -> None:
        """Run job on a fresh thread; both are released on the main thread once it ends. task names it for the
        confirmation before a quit or cancel stops it; jobs without one stop without asking. One job at a time:
        a second would orphan the first's thread, which Cancel and quit could then no longer reach."""
        if self.busy:
            log.warning("%s not started: %s is still running", task or "job", self.task or "another job")
            return
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
        self.thread, self.job, self.task, self.retry = thread, job, task, retry
        self.job_started = time.monotonic()
        if self.activity:
            self.activity.end()
        # a job the user started keeps the Mac from idle sleep and App Nap; a background check does not need to
        self.activity = macos.Activity(f"Vaquero: {task}") if task else None
        self.stopping = False
        self.progress_line = None  # each job draws its own progress line in the log
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

    def open_app_log(self) -> None:
        if logs.APP_LOG.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs.APP_LOG)))
        else:
            self._reveal(LOG_DIR)

    def open_last_report(self) -> None:
        reports = sorted(LOG_DIR.glob("vaquero-scan-*.csv"))
        if reports:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports[-1])))
        else:
            self.status.setText("No scan report yet")

    def _build_menus(self) -> None:
        """The menu bar. Items with a role move into the macOS application menu (Qt then drops the empty menu)."""
        bar = self.menuBar()

        def add(menu: QMenu, text: str, slot: Callable[[], object], keys: QKeySequence | QKeySequence.StandardKey | str | None = None,
                role: QAction.MenuRole = QAction.MenuRole.NoRole) -> QAction:
            action = QAction(text, self)
            action.setMenuRole(role)
            if keys is not None:
                action.setShortcut(keys)
            action.triggered.connect(lambda _=False: slot())
            menu.addAction(action)
            return action

        app = bar.addMenu("Vaquero")
        add(app, "About Vaquero", self.show_about, role=QAction.MenuRole.AboutRole)
        add(app, "Check for Updates…", self.check_updates, role=QAction.MenuRole.ApplicationSpecificRole)
        add(app, "Settings…", self.show_settings, QKeySequence.StandardKey.Preferences, QAction.MenuRole.PreferencesRole)
        add(app, "Quit Vaquero", self.quit_app, QKeySequence.StandardKey.Quit, QAction.MenuRole.QuitRole)

        file = bar.addMenu("File")
        self.scan_menu_action = add(file, "Scan", self.start_scan, "Ctrl+R")
        self.execute_menu_action = add(file, "Execute", self.execute, "Ctrl+E")
        self.cancel_menu_action = add(file, "Cancel", self.cancel_job, "Ctrl+.")
        file.addSeparator()
        add(file, "Open Last Scan Report", self.open_last_report)
        add(file, "Show Logs Folder", self.show_logs)
        file.addSeparator()
        add(file, "Close Window", self.close, QKeySequence.StandardKey.Close)

        edit = bar.addMenu("Edit")
        self.undo_menu_action = add(edit, "Undo", self.undo_last, QKeySequence.StandardKey.Undo)
        edit.addSeparator()
        for text, method, keys in (
            ("Cut", "cut", QKeySequence.StandardKey.Cut),
            ("Copy", "copy", QKeySequence.StandardKey.Copy),
            ("Paste", "paste", QKeySequence.StandardKey.Paste),
            ("Select All", "selectAll", QKeySequence.StandardKey.SelectAll),
        ):
            add(edit, text, lambda m=method: getattr(QApplication.focusWidget(), m, lambda: None)(), keys)

        window = bar.addMenu("Window")
        add(window, "Minimize", self.showMinimized, "Ctrl+M")
        add(window, "Zoom", lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        window.addSeparator()
        add(window, "Vaquero", self.show_window)

        helps = bar.addMenu("Help")
        add(helps, "Vaquero Help", self.show_help, QKeySequence.StandardKey.HelpContents)
        add(helps, "Open Log", self.open_app_log)
        add(helps, "Show Logs Folder", self.show_logs)
        helps.addSeparator()
        add(helps, "Vaquero Website", lambda: QDesktopServices.openUrl(QUrl(SITE_URL)))
        add(helps, "Report an Issue…", lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)))

        def refresh() -> None:
            busy, undo = self.busy, self.undo_text()
            self.scan_menu_action.setEnabled(not busy and bool(self.packs))
            self.execute_menu_action.setEnabled(not busy and bool(self._matches()))
            self.cancel_menu_action.setEnabled(busy)
            self.undo_menu_action.setText(undo or "Undo")
            self.undo_menu_action.setEnabled(bool(undo) and not busy)

        for menu in (file, edit):
            menu.aboutToShow.connect(refresh)
        refresh()

    def status_plain(self) -> str:
        return QTextDocumentFragment.fromHtml(self.status.text()).toPlainText().strip()

    def _last_run(self) -> list[JournalRow]:
        """The journal's newest undoable run, re-read only when the journal changed."""
        try:
            st = self.journal.stat()
        except OSError:
            return []
        key = (st.st_mtime, st.st_size)
        if self._undo_cache is None or self._undo_cache[0] != key:
            self._undo_cache = (key, last_run(self.journal))
        return self._undo_cache[1]

    def undo_text(self) -> str:
        """The Undo item's text for the newest copy or move, "" when there is nothing to undo."""
        rows = self._last_run()
        if not rows:
            return ""
        verb = "Move" if any(r.action.startswith("moved") for r in rows) else "Copy"
        return f"Undo {verb} of {len(rows)} File{'' if len(rows) == 1 else 's'}"

    def undo_last(self) -> None:
        rows = self._last_run()
        if not rows or self.busy:
            return
        self.show_window()
        moved = any(r.action.startswith("moved") for r in rows)
        try:
            when = datetime.strptime(rows[0].run[:15], "%Y%m%d-%H%M%S").strftime("%b %-d at %H:%M")
        except ValueError:
            when = "earlier"
        box = QMessageBox(self)
        box.setWindowTitle("Undo")
        box.setText(f"{self.undo_text()} from {when}?")
        box.setInformativeText(
            ("Moved files go back. " if moved else "")
            + ("Copies go to the Trash. " if not moved or any(r.action.startswith("copied") for r in rows) else "")
            + ("Overwritten files come back from the Trash." if any(r.trashed for r in rows) else "")
        )
        undo = box.addButton("Undo", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not undo:
            return
        section("Undo")
        log.info("undoing %d files from %s", len(rows), when, extra={"link": rows[0].dst.parent, "link_text": str(rows[0].dst.parent)})
        job = UndoWorker(rows, self.journal)
        job.progress.connect(lambda done, total: (
            self.status.setText(f"Undoing {status_value(f'{done} of {total}')}"),
            self._log_progress("undo", done, total, f"{done} of {total} files"),
        ), QUEUED)
        job.finished.connect(self._on_undo_finished, QUEUED)
        self._run(job, "the undo")

    def _on_undo_finished(self, counts: TransferCounts, cancelled: bool) -> None:
        self._undo_cache = None
        log.info("undo done: %d reversed, %d left alone, %d failed", counts.done, counts.skipped, counts.failed)
        back = [r for r in self.dropped if r.path.exists()]
        if back:
            self.results += back
            self.dropped = [r for r in self.dropped if r not in back]
        self._set_busy(False)
        self._update_match_count()
        self._done(f"undid {counts.done}, {counts.skipped} left alone, {counts.failed} failed", cancelled)

    def offer_move(self, bundle: Path, reason: str) -> None:
        """Vaquero opened from a disk image, Downloads, or a temporary copy: offer to put it in Applications,
        where updates, login items and the privacy settings it is given all stick."""
        box = QMessageBox(self)
        box.setWindowTitle("Move to Applications")
        box.setText("Move Vaquero to the Applications folder?")
        box.setInformativeText(reason)
        move = box.addButton("Move to Applications", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        never = QCheckBox("Do not ask again")
        box.setCheckBox(never)
        box.exec()
        if never.isChecked():
            self.settings.setValue(DONT_OFFER_MOVE_KEY, True)
        if box.clickedButton() is not move:
            return
        try:
            moved = access.move_to_applications(bundle)
        except (OSError, subprocess.CalledProcessError) as e:
            log.error("Vaquero was not moved: %s", logs.describe(e), exc_info=True)
            QMessageBox.warning(self, "Move to Applications", f"Vaquero could not be moved: {logs.describe(e)}")
            return
        # the copy opens once this one has quit, so it does not find this one holding the single-copy lock
        subprocess.Popen(
            ["/bin/sh", "-c", f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; open {shlex.quote(str(moved))}"],
            start_new_session=True,
        )
        self.quit_app()

    def show_window(self) -> None:
        if cocoa():
            macos.activate()
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def dock_shown(self) -> bool:
        return self.settings.value(SHOW_IN_DOCK_KEY, True, type=bool) or not self.menu_bar_shown()

    def menu_bar_shown(self) -> bool:
        return self.settings.value(SHOW_IN_MENU_BAR_KEY, True, type=bool)

    def toggle_dock(self) -> None:
        self.set_presence(dock=not self.dock_shown(), menu_bar=self.menu_bar_shown())

    def set_presence(self, dock: bool, menu_bar: bool) -> None:
        """Show Vaquero in the Dock, the menu bar, or both; never neither, or it could not be reached."""
        if not dock and not menu_bar:
            dock = True
        self.settings.setValue(SHOW_IN_DOCK_KEY, dock)
        self.settings.setValue(SHOW_IN_MENU_BAR_KEY, menu_bar)
        self.apply_presence()

    def apply_presence(self) -> None:
        dock, menu_bar = self.dock_shown(), self.menu_bar_shown()
        self.status_item.tray.setVisible(menu_bar)
        if cocoa() and macos.dock_visible() != dock:
            macos.set_dock_visible(dock)
            if self.isVisible():
                # leaving the Dock deactivates the app; bring the window back in front
                QTimer.singleShot(200, self.show_window)
        self.status_item.refresh()

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            if cocoa():
                macos.set_dock_badge("")
            # a click on the Dock icon brings back the window closed into the menu bar
            if self.dock_shown() and not self.isVisible() and not QApplication.activeModalWidget():
                self.show_window()

    @staticmethod
    def _reveal(folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(folder)], check=False)

    def show_settings(self) -> None:
        SettingsDialog(self).exec()

    def ensure_models(self) -> None:
        """First launch, or a damaged pack: fetch the face model pack before anything can be scanned."""
        installed = MODELS_DIR / DEFAULT_PACK
        if self.packs and not (installed.is_dir() and not remote.pack_intact(installed)):
            return
        job = TaskWorker(remote.ensure_models)
        self.bytes_text = "Downloading face detection model, please wait..."
        job.progress.connect(self._on_bytes, QUEUED)
        job.finished.connect(self._on_models_ready, QUEUED)
        self._run(job, "the face model download", retry=self.ensure_models)
        self.status.setText("Downloading face detection model, please wait...")
        section("Face model")
        log.info("Downloading face detection model (%s), please wait...", DEFAULT_PACK)

    def _on_bytes(self, done: int, total: int) -> None:
        self.status.setText(f"{self.bytes_text} {status_value(f'{done / 1e6:.0f} of {total / 1e6:.0f} MB')}")
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
        if self.busy:
            if quiet:  # the launch check waits for the model download
                self.after_job = lambda: self.check_updates(quiet=True)
            else:
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
        self._run(job, retry=None if quiet else self.check_updates)
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
        bundle = remote.running_bundle()
        if bundle is not None and (problem := remote.install_location_problem(bundle)):
            log.warning("update %s found but cannot be installed here: %s", rel.tag, problem)
            box = QMessageBox(self)
            box.setWindowTitle("Update available")
            box.setText(f"Vaquero {rel.tag.lstrip('v')} is available (you have {__version__}).")
            box.setInformativeText(problem)
            page = box.addButton("Open Download Page", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is page:
                QDesktopServices.openUrl(QUrl(SITE_URL))
            self.status.setText(f"Update {html.escape(rel.tag)} available; see the download page")
            return
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
            running = remote.running_bundle()
            if running is None:
                raise RemoteError("not running from an app bundle")
            # staged beside the installed app: the swap is then a rename on one volume
            workdir = Path(tempfile.mkdtemp(prefix=".vaquero-update-", dir=running.parent))
            try:
                staged = remote.stage_update(dmg, workdir)
                remote.verify_update(staged, running, rel.version)
            except BaseException:
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            finally:
                dmg.unlink(missing_ok=True)
            return staged

        job = TaskWorker(fetch)
        self.bytes_text = f"Downloading Vaquero {rel.tag}..."
        job.progress.connect(self._on_bytes, QUEUED)
        job.finished.connect(self._on_update_staged, QUEUED)
        self._run(job, "the update download", retry=self.check_updates)

    def _on_update_staged(self, staged: Path) -> None:
        bundle = remote.running_bundle()
        if bundle is None:
            return
        log.info("installing %s over %s and restarting", staged.name, bundle)
        try:
            remote.install_and_relaunch(staged, bundle, os.getpid())
        except Exception as e:
            log.error("update not installed: %s", logs.describe(e), exc_info=True)
            self._set_busy(False)
            self.status.setText(f"Update not installed: {html.escape(logs.describe(e))}")
            return
        # the download job has finished; clear it so the quit does not stop to ask about it
        self.task = None
        self.quit_app()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Vaquero",
            f"<h3 style='color:{theme.SIGNAL}'>Vaquero {__version__}</h3>"
            f"<p>{html.escape(SLOGAN)}</p>"
            "<p>Free, open source, no account, 100% private</p>"
            "<p>Runs entirely offline. No photo or face ever leaves your Mac.</p>"
            "<p>MIT licensed.</p>"
            "<p>Detection: SCRFD · Recognition: ArcFace (InsightFace model packs)<br>"
            "Inference: ONNX Runtime with Core ML on Apple Silicon</p>"
            "<p>InsightFace pretrained models are licensed for non-commercial research use only.</p>"
            '<p><a href="https://vaquero.bitwise0x.com">vaquero.bitwise0x.com</a> · '
            '<a href="https://github.com/BitWise-0x/Vaquero">github.com/BitWise-0x/Vaquero</a></p>',
        )

    def _on_thread_finished(self, thread: QThread, job: QObject) -> None:
        thread.wait()
        thread.finished.disconnect()  # the connection's lambda holds the only other references
        # a job started from the old one's finished handler may already own self.thread
        if self.thread is thread:
            self.thread = self.job = self.task = None
            self.status_item.set_working(False)
            if self.activity:
                self.activity.end()
                self.activity = None
            if then := self.after_job:
                self.after_job = None
                then()

    def check_access_at_launch(self) -> None:
        """Touch the saved folders off the main thread: macOS asks now, and a refusal gets the access dialog.

        Off the main thread because an unreachable network share can stall a directory read."""
        named = [
            (name, Path(e.text().strip()).expanduser())
            for name, e in (("Reference faces", self.refs), ("Folder to scan", self.source), ("Destination", self.dest))
            if e.text().strip()
        ]
        if not named:
            return

        def check() -> None:
            offline = [(name, drive) for name, p in named if (drive := logs.missing_volume(p))]
            reachable = [p for name, p in named if name not in {n for n, _ in offline}]
            self.access_checked.emit(blocked_folders(reachable), offline)

        self.access_checked.connect(self._on_access_checked, QUEUED)
        threading.Thread(target=check, daemon=True).start()

    def _on_access_checked(self, blocked: list[Path], offline: list[tuple[str, str]]) -> None:
        for name, drive in offline:
            log.warning("%s is on %s, which is not connected; connect it before scanning", name, drive)
        if offline:
            drives = sorted({drive for _, drive in offline})
            self.status.setText(f"Not connected: {html.escape(', '.join(drives))}. Connect before scanning.")
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
                self.status.setText("Waiting for access to " + html.escape(", ".join(b.name or str(b) for b in blocked)))
                return False
            blocked = blocked_folders(blocked)
        self.status.setText(READY)
        return True

    def start_scan(self) -> None:
        if self.packs.get(self.model.currentText()) is None:
            self.status.setText('No face model yet. <a href="models">Download it</a>')
            return
        self._preflight(self._start_scan)

    def _start_scan(self, refs: Path, source: Path, dest: Path) -> None:
        pack = self.packs.get(self.model.currentText())
        if pack is None:
            return
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
        job.listing.connect(self._on_listing, QUEUED)
        job.finished.connect(self._on_finished, QUEUED)
        self.live_found = 0
        self._show_found(0)
        self._run(job, "a scan")
        self.status.setText("Loading models and references")

    def _confirm_stop(self, title: str, stop: str) -> bool:
        """Ask before a quit or cancel stops a running scan, move, copy or download; True to go ahead."""
        if not self.task:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(f"{self.task[0].upper()}{self.task[1:]} is in progress.")
        go = box.addButton(stop, QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep running", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        return box.clickedButton() is go

    def cancel_job(self) -> None:
        # the job may end while the question is open
        if self.busy and self._confirm_stop("Cancel", "Stop") and self.job:
            self.task, self.stopping = None, True
            self.job.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText("Cancelling, finishing the current file")

    def _show_found(self, n: int) -> None:
        self.found.setText(f"Matches found: {status_value(str(n))}")

    def _on_progress(self, done: int, total: int, score: float) -> None:
        if score >= self.threshold.value():
            self.live_found += 1
        self.status.setText(f"Scanned: {status_value(f'{done} of {total}')}")
        self._log_progress("scan", done, total, f"{done} of {total} files")
        self._show_found(self.live_found)

    def _start_log(self, path: Path) -> None:
        """Each scan, and the move that follows it, gets its own log file and a cleared log view."""
        if self.file_log:
            log.removeHandler(self.file_log)
            self.file_log.close()
        self.file_log = logging.FileHandler(path, encoding="utf-8")
        self.file_log.setFormatter(FileFormatter(logs.FORMAT))
        log.addHandler(self.file_log)
        self.log_view.clear()
        self.progress_line = None
        self._banner()
        section("Setup")
        log.info("Vaquero %s, log file: %s", __version__, path, extra={"link": path, "link_text": str(path)})

    def _done(self, text: str, cancelled: bool = False) -> None:
        self.status.setText(f"<b>{'CANCELLED' if cancelled else 'DONE'}</b>: {status_value(text)}")
        self._tell_if_away(f"{'Cancelled' if cancelled else 'Done'}: {text}")

    def _tell_if_away(self, text: str, badge: str = "") -> None:
        """When a job ends while the window is hidden or another app is in front: bounce the Dock icon, badge it,
        and show a notification from the menu bar icon, as the settings allow."""
        if self.isVisible() and self.isActiveWindow():
            return
        if self.dock_shown():
            QApplication.alert(self)
            if badge and cocoa():
                macos.set_dock_badge(badge)
        if self.status_item.tray.isVisible() and self.settings.value(NOTIFY_KEY, True, type=bool):
            self.status_item.tray.showMessage("Vaquero", text, self.status_item.still, 8000)

    def _on_finished(self, results: list[ScanResult], cancelled: bool, refs: int, stopped: str) -> None:
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
        if stopped:
            log.error("scan stopped early: %s; the %d files scanned before that are listed", stopped, len(results))
        if review:
            log.warning(
                "%d match%s below %.2f look like lookalike territory; check them, or raise the threshold to drop them",
                review, "" if review == 1 else "es", REVIEW_BELOW,
            )
        summary = f"scanned {len(results)}, {len(matches)} matches at threshold {self.threshold.value():.2f}" + (
            f" ({review} to review)" if review else ""
        )
        self._done(f"Stopped: {stopped}. {summary}" if stopped else summary, cancelled)
        if matches and not (self.isVisible() and self.isActiveWindow()) and self.dock_shown() and cocoa():
            macos.set_dock_badge(str(len(matches)))

    def _drop_moved(self) -> None:
        """Forget results a move took away; without a file system call per result, which stalls on a dead share."""
        if isinstance(self.job, TransferWorker) and self.job.moved:
            self.dropped += [r for r in self.results if r.path in self.job.moved]
            self.results = [r for r in self.results if r.path not in self.job.moved]

    def _on_failed(self, msg: str) -> None:
        self._drop_moved()
        self._set_busy(False)
        if self.stopping:  # a stopped download ends this way: it resumes next time
            self._done(msg, cancelled=True)
        elif self.retry:
            self.status.setText(f'Failed: {html.escape(msg)} &nbsp;<a href="retry" style="color:{theme.SIGNAL}">Try again</a>')
        else:
            self.status.setText(f"Failed: {html.escape(msg)}")

    def _on_status_link(self, link: str) -> None:
        if link.startswith("https://"):
            QDesktopServices.openUrl(QUrl(link))
            return
        if self.busy:
            return
        if link == "retry" and (again := self.retry):
            self.retry = None
            again()
        elif link == "models":
            self.ensure_models()

    def execute(self) -> None:
        if self._matches():
            self._preflight(self._execute)

    def _execute(self, refs: Path, source: Path, dest: Path) -> None:
        matches = self._matches()
        if not matches:
            return
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
        self._run(job, f"a {mode.value}")

    def _on_listing(self, found: int) -> None:
        self.status.setText(f"Listing files {status_value(f'{found:,} found')}")

    def _on_transfer_progress(self, done: int, total: int) -> None:
        self.status.setText(f"{self._transfer_verb().capitalize()} {status_value(f'{done} of {total}')}")
        self._log_progress(self.transfer_mode.value, done, total, f"{done} of {total} files")

    def _on_transfer_finished(self, counts: TransferCounts, cancelled: bool, stopped: str) -> None:
        verb = self._transfer_verb()
        there = f", {counts.skipped} already there" if counts.skipped else ""
        log.info(
            "%s done: %d %s%s, %d failed%s",
            self.transfer_mode.value, counts.done, verb, there, counts.failed, " (cancelled)" if cancelled else "",
        )
        self._drop_moved()
        self._set_busy(False)
        self._update_match_count()
        summary = f"{verb} {counts.done}{there}, {counts.failed} failed. Undo list in {self.journal.name}"
        self._done(f"Stopped: {stopped}. {summary}" if stopped else summary, cancelled)

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
        detail += time_left(time.monotonic() - self.job_started, done, total)
        self.progress_state = (label, done, total, detail)
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

    def _open_link(self, url: QUrl, reveal: bool = False) -> None:
        """A file opens in its app and a folder in the Finder; with Option held, or reveal, the Finder shows where
        it is instead. Something no longer there points at the nearest folder that is."""
        path = Path(url.toLocalFile())
        reveal = reveal or bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
        if not path.exists():
            if drive := logs.missing_volume(path):
                self.status.setText(f"{html.escape(path.name)} is on {html.escape(drive)}, which is not connected")
                return
            near = next((p for p in path.parents if p.is_dir()), None)
            self.status.setText(f"No longer there: {html.escape(str(path))}")
            if near is not None and near != Path("/"):
                log.info("%s is no longer there; the nearest folder still there is %s", path.name, near, extra={"link": near, "link_text": str(near)})
            return
        if reveal:
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            QDesktopServices.openUrl(url)

    def _hover_link(self, url: QUrl) -> None:
        """The full path of a hovered link, since the log shows names relative to the scanned folder."""
        if url.isEmpty():
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(), f"{url.toLocalFile()}\nClick to open, Option-click to show in Finder", self.log_view)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.settings.setValue("geometry", self.saveGeometry())
        if not self.quitting and self.status_item.tray.isVisible():
            # with the menu bar icon, closing the window keeps Vaquero, and any job, running there
            event.ignore()
            self.hide()
            if not self.settings.value(MENU_BAR_HINT_KEY, False, type=bool):
                self.settings.setValue(MENU_BAR_HINT_KEY, True)
                self.status_item.tray.showMessage(
                    "Vaquero", "Vaquero is still running in the menu bar.", self.status_item.still, 6000
                )
            return
        if self.busy and not self._confirm_stop("Quit Vaquero", "Quit"):
            self.quitting = False
            event.ignore()
            return
        if self.job and self.thread:
            # asked once; closing again while it finishes the current file does not ask again
            self.task, self.stopping = None, True
            self.job.cancel()
            # a running QThread destroyed at exit aborts the process; the job stops after its current file
            if not self.thread.wait(CLOSE_WAIT_MS):
                self.status.setText("Still finishing the current file; quit again in a moment")
                self.quitting = False
                event.ignore()
                return
        if self.file_log:
            self.file_log.close()
        self.status_item.tray.hide()
        super().closeEvent(event)
        QApplication.quit()

    def quit_app(self) -> None:
        """Quit for real: from the menus, the menu bar icon, the Dock, or a logout."""
        self.quitting = True
        if self.busy:  # the question about the running job needs the window in front, even from the menu bar
            self.show_window()
        if not self.close():
            self.quitting = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        # macOS asks to quit (Dock menu, logout, restart) with a Quit event; the window must not just hide then
        if event.type() == QEvent.Type.Quit:
            self.quitting = True
        return False


def _qt_message(kind: QtMsgType, context: QMessageLogContext, message: str) -> None:
    """Qt's own warnings, which otherwise go to stderr and are lost in an app bundle."""
    quiet = {"file_only": True}  # for the log file; the window's log is for the user's own runs
    if kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        log.error("Qt: %s", message, extra=quiet)
    elif kind == QtMsgType.QtWarningMsg:
        log.warning("Qt: %s", message, extra=quiet)


def main() -> None:
    log.setLevel(logging.INFO)
    remote.migrate_legacy()
    logs.install_crash_hooks()
    logs.start_app_log()
    qInstallMessageHandler(_qt_message)
    log.info("Vaquero %s starting on macOS %s (%s)", __version__, platform.mac_ver()[0], platform.machine())
    if removed := logs.prune_scan_logs(QSettings("BitWise", "Vaquero").value(KEEP_SCANS_KEY, logs.SCANS_KEPT, type=int)):
        log.info("removed %d old scan logs and reports", removed)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # the window can close into the menu bar; quitting is explicit
    lock = QLockFile(str(remote.SUPPORT_DIR / "vaquero.lock"))
    remote.SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not lock.tryLock(200):
        # two copies would share the partial downloads and the undo journal
        log.warning("Vaquero is already running (%s)", lock.getLockInfo())
        QMessageBox.information(None, "Vaquero", "Vaquero is already open. Find it in the Dock or the menu bar.")
        sys.exit(0)
    theme.apply(app, asset("check.svg").as_posix())
    w = Window()
    app.installEventFilter(w)
    app.applicationStateChanged.connect(w._on_app_state)
    w.apply_presence()
    w.show()
    bundle = remote.running_bundle()
    if bundle and not w.settings.value(DONT_OFFER_MOVE_KEY, False, type=bool) and (why := access.move_offer_reason(bundle)):
        QTimer.singleShot(0, lambda: w.offer_move(bundle, why))
    if failed := remote.take_update_failure():
        log.error(
            "the last update was not installed: %s; see %s", failed, remote.UPDATE_LOG,
            extra={"link": remote.UPDATE_LOG, "link_text": str(remote.UPDATE_LOG)},
        )
        w.status.setText(
            f"The last update was not installed: {html.escape(failed)}. Download it from "
            f'<a href="{SITE_URL}" style="color:{theme.SIGNAL}">the website</a>.'
        )
    w.ensure_models()
    w.check_access_at_launch()
    if w.settings.value(AUTO_UPDATE_KEY, True, type=bool):
        w.check_updates(quiet=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
