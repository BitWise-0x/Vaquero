"""Captures Vaquero's menu bar icon and its menu from the real macOS menu bar during a sample scan, with throwaway
settings:

- docs/screenshots/menubar-icon.webp: the amber scan line sweeping across the icon, drawn from the app's own frames
- docs/screenshots/menubar.webp: the menu opened as a click opens it, its progress line moving while it stays open
- docs/screenshots/menubar.png: one frame of it

Only the menu is captured from the screen, and only while it is confirmed open; the icon is the app's own frame
drawn on a plain menu bar, so nothing else on the screen shows. A key press or click closes an open menu; the run
reopens it, and stops without writing the menu files if it keeps closing. macOS asks once to let the terminal
record the screen. Exits 1 on any failure.

Run: QT_QPA_PLATFORM=cocoa uv run --with pillow python scripts/menubar_screenshots.py
"""

import ctypes
import io
import os
import subprocess
import sys
import tempfile
import time
from ctypes import c_bool, c_ulong, c_void_p
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QSettings, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from vaquero import app as ui
from vaquero import macos, menubar

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
TOTAL, START = 2486, 380
ICON_FRAMES = menubar.SWEEP_FRAMES + menubar.HOLD_FRAMES
MENU_FRAMES, MENU_FRAME_MS, HOLD_MS, MAX_REOPENS = 26, 260, 1600, 40
STEP = (TOTAL - START) // (MENU_FRAMES + 6)
BAR, BACKDROP = (22, 22, 24), (14, 15, 16)  # a dark menu bar, and the app's own background below it
SCALE = 2  # screencapture saves Retina pixels
WEBP_QUALITY = 90  # the menu's glass makes lossless frames heavy; text stays sharp at this quality
ICON_PAD = 12  # room on either side of the icon in its slot, in pixels


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


def menu_rect() -> tuple[int, int, int, int] | None:
    """Where the open menu is on screen, top-left origin, in points; None when no menu of this app is open.

    AppKit shows an open menu in an NSPopupMenuWindow. Its frame is the only reliable source: QMenu.geometry() is
    not updated when macOS opens the menu from the status item."""
    with macos._pool():
        windows = macos._fn(c_void_p)(macos._app(), macos._sel("windows"))
        for i in range(macos._fn(c_ulong)(windows, macos._sel("count"))):
            window = macos._fn(c_void_p, c_ulong)(windows, macos._sel("objectAtIndex:"), i)
            name = macos._utf8(macos._fn(c_void_p)(window, macos._sel("className")))
            if name == "NSPopupMenuWindow" and macos._fn(c_bool)(window, macos._sel("isVisible")):
                r = macos._fn(_Rect)(window, macos._sel("frame"))
                top = QGuiApplication.primaryScreen().geometry().height() - r.y - r.h  # AppKit counts from the bottom
                return int(r.x), int(top), int(r.w), int(r.h)
    return None


def capture(rect: tuple[int, int, int, int], path: Path) -> Image.Image:
    x, y, w, h = rect
    subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True)
    with Image.open(path) as im:
        return im.convert("RGB")


def icon_image(item: menubar.StatusItem, frame: int, height: int) -> Image.Image:
    """The icon as the menu bar shows it: frame of the app's own sweep, 2x, centered in a bar of height."""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    item.frames[frame].pixmap(36, 36).save(buf, "PNG")
    glyph = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    slot = Image.new("RGBA", (glyph.width + 2 * ICON_PAD, height), BAR + (255,))
    slot.alpha_composite(glyph, (ICON_PAD, (height - glyph.height) // 2))
    return slot.convert("RGB")


def even(frames: list[Image.Image], bar_height: int) -> list[Image.Image]:
    """The menu widens and narrows with its progress line; every frame of an animation needs the same size."""
    size = (max(f.width for f in frames), max(f.height for f in frames))
    out = []
    for f in frames:
        canvas = Image.new("RGB", size, BACKDROP)
        canvas.paste(Image.new("RGB", (size[0], bar_height), BAR), (0, 0))
        canvas.paste(f, (0, 0))
        out.append(canvas)
    return out


def main() -> int:
    failures: list[str] = []
    tmp = tempfile.TemporaryDirectory()
    shots = Path(tmp.name)
    ui.QSettings = lambda *_: QSettings(str(shots / "vaquero.ini"), QSettings.Format.IniFormat)
    qa = QApplication([])
    qa.setQuitOnLastWindowClosed(False)
    w = ui.Window()
    w.apply_presence()
    item = w.status_item
    done = [START]
    bar_height = [0]
    menu_frames: list[Image.Image] = []

    def scan_to(n: int) -> None:
        done[0] = n
        w.job_started = time.monotonic() - n / 9  # a plausible pace for the time left
        w._on_progress(n, TOTAL, 0.1)

    def end(code: int) -> None:
        tmp.cleanup()
        print("menu bar screenshots", "failed: " + ", ".join(failures) if failures else "written", flush=True)
        os._exit(code)  # also closes the open menu

    def icon_webp() -> None:
        bar_height = item.tray.geometry().height() * SCALE
        frames = [icon_image(item, n, bar_height) for n in range(ICON_FRAMES)]
        frames[0].save(
            OUT / "menubar-icon.webp", save_all=True, append_images=frames[1:],
            duration=menubar.FRAME_MS, loop=0, lossless=True,
        )
        print(f"wrote {OUT / 'menubar-icon.webp'} ({len(frames)} frames)", flush=True)

    def click() -> None:
        # a click on the icon, as a person makes it: AppKit opens the menu and tracks it from here
        macos._fn(None, c_void_p)(macos._status_button(), macos._sel("performClick:"), None)

    def open_menu() -> None:
        item.timer.start()  # the icon goes on sweeping while the menu is open, as during a real scan
        QTimer.singleShot(700, lambda: menu_shot(0))
        click()

    def menu_shot(n: int, tries: int = 0) -> None:
        # a key press or click anywhere closes an open menu; reopen it, and keep only frames taken while it was open
        rect = menu_rect()
        if rect is None:
            if tries >= MAX_REOPENS:
                failures.append("the menu kept closing (keyboard or mouse in use?)")
                end(1)
            click()
            QTimer.singleShot(400, lambda: menu_shot(n, tries + 1))
            return
        geo = item.tray.geometry()
        icon = icon_image(item, item.frame, geo.height() * SCALE)
        body = capture(rect, shots / "menu.png")
        if menu_rect() != rect:  # it closed or moved while being captured: the frame may show what was behind it
            QTimer.singleShot(50, lambda: menu_shot(n, tries + 1))
            return
        pad = 12 * SCALE
        frame = Image.new("RGB", (body.width + 2 * pad, icon.height + body.height + pad + 4 * SCALE), BACKDROP)
        frame.paste(Image.new("RGB", (frame.width, icon.height), BAR), (0, 0))
        frame.paste(icon, (pad + (geo.left() - rect[0]) * SCALE + (geo.width() * SCALE - icon.width) // 2, 0))
        frame.paste(body, (pad, icon.height + 4 * SCALE))
        menu_frames.append(frame)
        bar_height[0] = icon.height
        if n + 1 < MENU_FRAMES:
            scan_to(done[0] + STEP)
            QTimer.singleShot(MENU_FRAME_MS, lambda: menu_shot(n + 1))
            return
        if menu_frames[0].tobytes() == menu_frames[-1].tobytes():
            failures.append("the open menu did not follow the scan")
            end(1)
        frames = even(menu_frames, bar_height[0])
        frames[MENU_FRAMES // 2].save(OUT / "menubar.png", optimize=True)
        frames[0].save(
            OUT / "menubar.webp", save_all=True, append_images=frames[1:],
            duration=[MENU_FRAME_MS] * (MENU_FRAMES - 1) + [HOLD_MS], loop=0, quality=WEBP_QUALITY, method=6,
        )
        print(f"wrote {OUT / 'menubar.png'} and {OUT / 'menubar.webp'} ({MENU_FRAMES} frames)", flush=True)
        end(0)

    def start() -> None:
        w.job, w.task = ui.TaskWorker(lambda progress, cancelled: None), "a scan"
        scan_to(START)
        item.set_working(True)  # once the icon is on screen: its frames match this menu bar's light or dark
        icon_webp()
        QTimer.singleShot(300, open_menu)

    QTimer.singleShot(800, start)
    QTimer.singleShot(90000, lambda: (failures.append("timed out"), end(1)))
    qa.exec()
    return 1


if __name__ == "__main__":
    sys.exit(main())
