"""Renders the screenshots into docs/screenshots from the real window at 2x, fed with sample data: stills as
PNG (results, the log's right-click menu, help, settings, the Undo confirmation), and the runs that show progress
(first launch, a scan, a scan through to a copy) as animated WebP. The menu bar ones come from the real menu bar:
scripts/menubar_screenshots.py.

Settings go to a throwaway file and no log file is written, so the saved folders of whoever runs this are
neither shown nor changed.

Run: QT_QPA_PLATFORM=offscreen uv run python scripts/screenshots.py
"""

import csv
import io
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QBuffer, QIODevice, QPoint, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from vaquero import app as ui
from vaquero import remote
from vaquero import settings, theme
from vaquero.engine import ScanResult, Transfer

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
FILES_PER_S, BYTES_PER_S = 9, 18e6
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


def pil(pixmap: object, mode: str = "RGB") -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")  # type: ignore[attr-defined]
    return Image.open(io.BytesIO(bytes(buf.data()))).convert(mode)


def popup(menu_image: Image.Image) -> tuple[Image.Image, Image.Image]:
    """A menu drawn off screen has no panel: put it on one in the app's menu colors, with the border and soft
    shadow macOS gives an open menu. Returns the menu and its shadow, both RGBA."""
    radius, border = 14, 2
    panel = Image.new("RGBA", menu_image.size, theme.SURFACE)
    panel.alpha_composite(menu_image)
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel.width - 1, panel.height - 1), radius, fill=255)
    panel.putalpha(mask)
    ImageDraw.Draw(panel).rounded_rectangle(
        (0, 0, panel.width - 1, panel.height - 1), radius, outline=theme.LINE_STRONG, width=border
    )
    shadow = Image.new("RGBA", (panel.width + 80, panel.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((40, 52, 40 + panel.width, 52 + panel.height), radius, fill=(0, 0, 0, 170))
    return panel, shadow.filter(ImageFilter.GaussianBlur(18))


def log_menu_shot(w: ui.Window, name: str) -> None:
    """The window with the log's right-click menu open on a match, as the app draws it."""
    first = SAMPLE[0][0]
    cursor = w.log_view.document().find(first)
    rect = w.log_view.cursorRect(cursor)
    at = QPoint(rect.left() - 40, rect.center().y())
    menu = w.log_view.link_menu(at)
    menu.adjustSize()
    for action in menu.actions():  # macOS never underlines a menu's keyboard hints; the offscreen renderer does
        action.setText(action.text().replace("&", ""))
    menu.setActiveAction(menu.actions()[0])  # the pointer on Open
    QApplication.processEvents()
    scale = 2  # QT_SCALE_FACTOR
    shot = pil(w.grab(), "RGBA")
    corner = w.log_view.viewport().mapTo(w, at)
    x, y = corner.x() * scale + 6, corner.y() * scale + 6
    panel, shadow = popup(pil(menu.grab(), "RGBA"))
    shot.alpha_composite(shadow, (x - 40, y - 40))
    shot.alpha_composite(panel, (x, y))
    shot.convert("RGB").save(OUT / name)
    print(f"wrote {OUT / name}")


def grab(_: QApplication, widget: object, name: str) -> None:
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))  # type: ignore[attr-defined]
    print(f"wrote {OUT / name}")


FRAME_MS = 80
HOLD_MS = 2600  # the last frame of an animation stays up this long before it loops


class Recorder:
    """Collects grabs of the window as animation frames and writes them as one looping animated WebP. The header's
    scan line runs on the animation's own clock, so it sweeps as in the app however long each grab takes."""

    def __init__(self, widget: ui.Window) -> None:
        self.widget = widget
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []
        self.elapsed = 0

    def frame(self) -> Image.Image:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        self.widget.grab().save(buf, "PNG")
        return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")

    def snap(self, ms: int = FRAME_MS) -> None:
        QApplication.processEvents()
        logo = self.widget.logo
        if not logo.timer.isActive():
            logo.timer.start(logo.FRAME_MS)
        logo.started = time.monotonic() - self.elapsed / 1000
        self.frames.append(self.frame())
        self.durations.append(ms)
        self.elapsed += ms

    def save(self, name: str) -> None:
        # the last frame holds before the loop, so it shows the icon between sweeps
        self.widget.logo.stop()
        self.frames[-1] = self.frame()
        self.durations[-1] = HOLD_MS
        first, *rest = self.frames
        first.save(OUT / name, save_all=True, append_images=rest, duration=self.durations, loop=0, lossless=True, method=6)
        print(f"wrote {OUT / name} ({len(self.frames)} frames)")


def sample_results() -> list[ScanResult]:
    results = [ScanResult(SOURCE / name, "image", score, 1, agree) for name, score, agree in SAMPLE]
    return results + [ScanResult(SOURCE / f"IMG_{i:04d}.JPG", "image", 0.05, 1, 0) for i in range(TOTAL - len(SAMPLE))]


def scan_frames(w: ui.Window, rec: Recorder, steps: int) -> None:
    """A scan from its first file to its last, a frame per step; matches turn up as it goes."""
    w.progress_line = None
    w.live_found = 0
    scan_setup(w)
    QApplication.processEvents()  # log lines arrive queued; let them land before the progress line
    w._set_busy(True)
    hits = {round(TOTAL * k / (len(SAMPLE) + 1)) for k in range(1, len(SAMPLE) + 1)}
    done = 0
    for step in range(1, steps + 1):
        target = TOTAL * step // steps
        while done < target:
            done += 1
            w.job_started = time.monotonic() - done / FILES_PER_S  # a plausible pace for the time left
            w._on_progress(done, TOTAL, 0.6 if done in hits else 0.1)
        rec.snap()


def animate(w: ui.Window) -> None:
    # first launch: the face model downloads, then the app is ready
    w.progress_line = None
    w.live_found = 0
    w._show_found(0)
    w.log_view.clear()
    w._banner()
    ui.section("Face model")
    ui.log.info("Downloading face detection model (%s), please wait...", ui.DEFAULT_PACK)
    QApplication.processEvents()
    w.bytes_text = "Downloading face detection model, please wait..."
    w._set_busy(True)
    rec = Recorder(w)
    size = remote.MODEL_ARCHIVE.size
    for step in range(31):
        w.job_started = time.monotonic() - size * step / 30 / BYTES_PER_S
        w._on_bytes(size * step // 30, size)
        rec.snap()
    w._set_busy(False)
    ui.log.info("face detection model installed, ready to scan")
    w._done("face detection model installed")
    rec.snap()
    rec.save("first-launch.webp")

    # a scan running
    rec = Recorder(w)
    scan_frames(w, rec, 40)
    rec.save("scanning.webp")

    # a scan through to its matches, then a copy of them
    w.resize(w.width(), 1050)
    rec = Recorder(w)
    scan_frames(w, rec, 24)
    w._on_finished(sample_results(), False, REFS_KEPT, "")
    w.execute_btn.setEnabled(True)
    w.log_view.verticalScrollBar().setValue(0)
    for _ in range(18):  # the matches stay up a moment before the copy starts
        rec.snap()
    w.transfer_mode = Transfer.COPY
    ui.section("Copy")
    ui.log.info("copying %d files to %s", len(SAMPLE), DEST, extra={"link": DEST, "link_text": str(DEST)})
    QApplication.processEvents()
    w._set_busy(True)
    for done in range(len(SAMPLE) + 1):
        w.job_started = time.monotonic() - done * 2
        w._on_transfer_progress(done, len(SAMPLE))
        rec.snap(160)
    ui.log.info("copy done: %d copied, 0 failed", len(SAMPLE))
    w._set_busy(False)
    w._done(f"copied {len(SAMPLE)}, 0 failed. Undo list in vaquero-moves.csv")
    w.log_view.verticalScrollBar().setValue(w.log_view.verticalScrollBar().maximum())
    rec.snap()
    rec.save("scan-and-copy.webp")


def main() -> None:
    ui.log.setLevel(logging.INFO)
    os.environ["QT_SCALE_FACTOR"] = "2"  # Retina pixels; offscreen renders at 1x otherwise
    qa = QApplication(sys.argv)
    theme.apply(qa, ui.asset("check.svg").as_posix())
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        sandbox(Path(tmp))

        w = ui.Window()
        w.menuBar().hide()  # macOS puts the menu in the system bar; offscreen draws it in the window
        w.logo.stop()  # stills show the plain icon; Recorder runs the scan line in the animations
        w.show()
        fill(w)

        # scan finished, matches listed
        w.progress_line = None
        scan_setup(w)
        QApplication.processEvents()
        w._on_progress(TOTAL, TOTAL, 0.1)
        w._on_finished(sample_results(), False, REFS_KEPT, "")
        w._set_busy(False)
        w.execute_btn.setEnabled(True)
        # tall enough that the whole log, buffalo banner included, fits without scrolling
        w.resize(w.width(), 1050)
        QApplication.processEvents()
        w.log_view.verticalScrollBar().setValue(0)
        grab(qa, w, "results.png")
        log_menu_shot(w, "log-menu.png")

        w.resize(w.width(), w.sizeHint().height())
        animate(w)

        def dialog_shot(name: str) -> None:
            dialog = next(x for x in qa.topLevelWidgets() if isinstance(x, QDialog) and x.isVisible())
            grab(qa, dialog, name)
            dialog.reject()

        QTimer.singleShot(300, lambda: dialog_shot("help.png"))
        w.show_help()
        # the settings dialog lists its folders and their state; show the sample home, not the real one
        ui.LOG_DIR = settings.LOG_DIR = LOG_FILE.parent
        ui.MODELS_DIR = settings.MODELS_DIR = Path("/Users/you/Library/Application Support/Vaquero/models")
        settings.JOURNAL = Path("/Users/you/Library/Application Support/Vaquero/vaquero-moves.csv")
        settings.access.folder_state = lambda text: settings.access.FolderState.OK
        settings.access.full_disk_access = lambda: False
        settings.remote.running_bundle = lambda: Path("/Applications/Vaquero.app")
        settings.macos.login_item_status = lambda: settings.macos.LOGIN_OFF
        QTimer.singleShot(300, lambda: dialog_shot("settings.png"))
        w.show_settings()
        w.job = None

        # Edit > Undo after the sample copy: the confirmation it asks first
        journal = Path(tmp) / "vaquero-moves.csv"
        with journal.open("w", newline="") as fh:
            rows = csv.writer(fh)
            for name, _, _ in SAMPLE:
                rows.writerow([SOURCE / name, DEST / Path(name).name, "copied", "20250301-101500-000000", ""])
        w.journal, w._undo_cache = journal, None
        QTimer.singleShot(300, lambda: dialog_shot("undo.png"))
        w.undo_last()
        w.close()


if __name__ == "__main__":
    main()
