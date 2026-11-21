"""Captures the menu bar icon and its menu from the real macOS menu bar (cocoa, not offscreen: the menu is a native
one), during a sample scan: docs/screenshots/menubar.png (the open menu) and docs/screenshots/menubar-icon.webp
(the icon sweeping, then still). Settings go to a throwaway file. Needs a logged-in session; macOS asks once to
let the terminal record the screen.

Run: QT_QPA_PLATFORM=cocoa uv run --with pillow python scripts/menubar_screenshots.py
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from vaquero import app as ui
from vaquero import menubar

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
TOTAL, DONE = 2486, 1012
FRAMES = menubar.SWEEP_FRAMES + menubar.HOLD_FRAMES
BAR, BACKDROP = (22, 22, 24), (14, 15, 16)  # a dark menu bar, and the app's own background below it


def capture(rect: tuple[int, int, int, int], path: Path) -> Image.Image:
    x, y, w, h = rect
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True)
    return Image.open(path)


def main() -> int:
    ini = Path(tempfile.mkdtemp()) / "vaquero.ini"
    ui.QSettings = lambda *_: QSettings(str(ini), QSettings.Format.IniFormat)
    qa = QApplication([])
    qa.setQuitOnLastWindowClosed(False)
    w = ui.Window()
    w.apply_presence()
    item = w.status_item
    tmp = Path(tempfile.mkdtemp())

    # a scan a good way along, as the menu shows it
    w.job, w.task = ui.TaskWorker(lambda progress, cancelled: None), "a scan"
    w.job_started = time.monotonic() - DONE / 9
    w._on_progress(DONE, TOTAL, 0.1)
    w.status.setText(f"Scanned: {ui.status_value(f'{DONE} of {TOTAL}')}")
    item.set_working(True)
    frames: list[Image.Image] = []

    def icon_frames(n: int = 0) -> None:
        x, y, width, height = item.tray.geometry().getRect()
        item.frame = n % FRAMES
        item.tray.setIcon(item.frames[item.frame])
        QApplication.processEvents()
        QTimer.singleShot(60, lambda: shoot(n, (x - 6, y, width + 12, height)))

    def shoot(n: int, rect: tuple[int, int, int, int]) -> None:
        frames.append(capture(rect, tmp / f"icon{n}.png").convert("RGB"))
        if n + 1 < FRAMES:
            icon_frames(n + 1)
        else:
            item.timer.stop()
            frames[0].save(
                OUT / "menubar-icon.webp", save_all=True, append_images=frames[1:],
                duration=menubar.FRAME_MS, loop=0, lossless=True,
            )
            print(f"wrote {OUT / 'menubar-icon.webp'} ({len(frames)} frames)")
            QTimer.singleShot(200, open_menu)

    def open_menu() -> None:
        item.refresh()
        geo = item.tray.geometry()
        QTimer.singleShot(700, lambda: shoot_menu(geo))
        item.menu.popup(QPoint(geo.left(), geo.bottom() + 4))

    def shoot_menu(geo: object) -> None:
        # only Vaquero's icon and its menu: the rest of the menu bar and the desktop are whoever runs this
        menu = item.menu.geometry()
        scale = 2  # screencapture saves Retina pixels
        icon = capture((geo.left(), geo.top(), geo.width(), geo.height()), tmp / "icon.png").convert("RGB")
        body = capture((menu.left(), menu.top(), menu.width(), menu.height()), tmp / "menu.png").convert("RGB")
        pad = 12 * scale
        bar_h = icon.height
        canvas = Image.new("RGB", (body.width + 2 * pad, bar_h + body.height + pad + 4 * scale), BAR)
        canvas.paste(Image.new("RGB", (canvas.width, canvas.height - bar_h), BACKDROP), (0, bar_h))
        canvas.paste(icon, (pad + (geo.left() - menu.left()) * scale, 0))
        canvas.paste(body, (pad, bar_h + 4 * scale))
        canvas.save(OUT / "menubar.png", optimize=True)
        print(f"wrote {OUT / 'menubar.png'}")
        item.menu.hide()
        w.job = w.task = None
        qa.quit()

    QTimer.singleShot(800, icon_frames)
    QTimer.singleShot(30000, qa.quit)
    qa.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
