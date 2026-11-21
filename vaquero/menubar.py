"""Vaquero's menu bar icon and its menu: what is running, how far along, and the actions that need no window.

Idle, the icon is a template image, which macOS tints for whatever is behind the menu bar. While a scan, copy, move
or download runs, the amber scan line sweeps across it as in the window header: those frames are drawn in color,
the buffalo and corners in the menu bar's own light or dark, read from the icon's appearance each sweep.

The menu opens on the progress of the running job: an amber bar with the percentage, then what is running and the
time left. It is a view of its own inside the menu: macOS draws menu item images at icon size, and a view keeps
redrawing while the menu stays open."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget, QWidgetAction

from vaquero import macos, theme
from vaquero.logs import shorten

if TYPE_CHECKING:
    from vaquero.app import Window

SIZES = (18, 36)  # 18 pt at 1x and 2x
GLOW_ALPHA = (0.28, 0.55, 1.0)  # the scan line's halo, inner halo and core, as in the window header
SWEEP_FRAMES, HOLD_FRAMES, FRAME_MS = 16, 8, 125  # a 2 s sweep, then 1 s still, at 8 frames a second
INSET = 0.14  # the line stays inside the viewfinder corners, as in the window header


def _frame(base: QPixmap, glyph: QColor, progress: float | None) -> QPixmap:
    """base, the black template glyph, recolored to glyph, with the amber scan line glowing at progress (0 top,
    1 bottom) as in the window header; no line when progress is None."""
    pix = base.copy()
    p = QPainter(pix)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pix.rect(), glyph)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    if progress is None:
        p.end()
        return pix
    edge = pix.width()
    lo, hi = edge * INSET, edge * (1 - INSET)
    y = lo + progress * (hi - lo)
    core = max(1.0, edge / 18)
    for alpha, thick in zip(GLOW_ALPHA, (core * 3.5, core * 2, core), strict=True):
        color = QColor(theme.SIGNAL)
        color.setAlphaF(alpha)
        p.fillRect(QRectF(lo - core, y - thick / 2, hi - lo + 2 * core, thick), color)
    p.end()
    return pix


def color_icon(pixmaps: list[QPixmap]) -> QIcon:
    icon = QIcon()
    for pix in pixmaps:
        icon.addPixmap(pix)
    return icon


def template_icon(pixmaps: list[QPixmap]) -> QIcon:
    icon = QIcon()
    for pix in pixmaps:
        icon.addPixmap(pix)
    icon.setIsMask(True)
    return icon


def fraction(done: int, total: int) -> float:
    return min(done / total, 1.0) if total > 0 else 0.0


VERBS = {"scan": "Scanning", "download": "Downloading", "copy": "Copying", "move": "Moving", "undo": "Undoing"}


def progress_text(label: str, done: int, total: int) -> str:
    """The line under the bar, short enough never to wrap: what is running and how far along."""
    verb = VERBS.get(label, label.capitalize())
    if label == "download":
        return f"{verb} {done / 1e6:,.0f} of {total / 1e6:,.0f} MB"
    return f"{verb} {done:,} of {total:,}"


def menu_is_dark() -> bool:
    """Menus follow the system appearance."""
    return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light


class ProgressView(QWidget):
    """The top of the menu while a job runs: a wide amber bar with the percentage beside it, and under it what is
    running on the left and the time left on the right, lined up with the menu's item text. A fixed size, so the
    menu keeps its width as the numbers change."""

    WIDTH, HEIGHT = 320, 56
    INSET, RIGHT = 30, 18  # the menu's item text starts this far in; room on the right
    BAR_Y, BAR_H, PCT_W, GAP = 11, 10, 46, 8
    TEXT_PT, PCT_PT = 12.5, 14

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.frac, self.line, self.left, self.dark = 0.0, "", "", True

    def show_state(self, frac: float, line: str, left: str, dark: bool) -> None:
        if (frac, line, left, dark) != (self.frac, self.line, self.left, self.dark):
            self.frac, self.line, self.left, self.dark = frac, line, left, dark
            self.update()

    def paintEvent(self, _: object) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = QColor(255, 255, 255) if self.dark else QColor(0, 0, 0)
        track = QColor(ink)
        track.setAlphaF(0.18)
        bar_w = self.WIDTH - self.INSET - self.RIGHT - self.PCT_W - self.GAP
        r = self.BAR_H / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(self.INSET, self.BAR_Y, bar_w, self.BAR_H), r, r)
        if self.frac > 0:
            p.setBrush(QColor(theme.SIGNAL))
            p.drawRoundedRect(QRectF(self.INSET, self.BAR_Y, max(self.BAR_H, bar_w * self.frac), self.BAR_H), r, r)

        pct = QFont(self.font())
        pct.setPointSizeF(self.PCT_PT)
        pct.setBold(True)
        p.setFont(pct)
        p.setPen(QColor(theme.SIGNAL))
        box = QRectF(self.WIDTH - self.RIGHT - self.PCT_W, self.BAR_Y - 6, self.PCT_W, self.BAR_H + 12)
        p.drawText(box, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self.frac:.0%}")

        text = QFont(self.font())
        text.setPointSizeF(self.TEXT_PT)
        p.setFont(text)
        metrics = QFontMetrics(text)
        top = self.BAR_Y + self.BAR_H + 9
        width = self.WIDTH - self.INSET - self.RIGHT
        left_w = metrics.horizontalAdvance(self.left) if self.left else 0
        soft = QColor(ink)
        soft.setAlphaF(0.55)
        p.setPen(soft)
        p.drawText(QRectF(self.INSET, top, width, 20), Qt.AlignmentFlag.AlignRight, self.left)
        main = QColor(ink)
        main.setAlphaF(0.85)
        p.setPen(main)
        room = width - left_w - (12 if left_w else 0)
        p.drawText(QRectF(self.INSET, top, room, 20), Qt.AlignmentFlag.AlignLeft, self.fit_line(metrics, room))
        p.end()

    def fit_line(self, metrics: QFontMetrics, room: int) -> str:
        """The line as it fits beside the time left: whole, else without its verb ("288 of 289 MB"), and cut short
        only if even that does not fit."""
        if metrics.horizontalAdvance(self.line) <= room:
            return self.line
        rest = self.line.split(" ", 1)[-1]
        if metrics.horizontalAdvance(rest) <= room:
            return rest
        return metrics.elidedText(rest, Qt.TextElideMode.ElideRight, room)


class StatusItem(QObject):
    def __init__(self, window: Window, asset: Callable[[str], object]) -> None:
        super().__init__(window)
        self.window = window
        self.bases = [QPixmap(str(asset(f"MenuBar-{size}.png"))) for size in SIZES]
        self.still = template_icon(self.bases)
        self.dark: bool | None = None  # the menu bar the working frames were drawn for
        self.frames: list[QIcon] = []
        self._draw_frames(dark=True)
        self.frame = 0

        self.tray = QSystemTrayIcon(self.still, self)
        self.tray.setToolTip("Vaquero")
        self.menu = QMenu()
        self.progress_view = ProgressView()
        self.progress_action = QWidgetAction(self.menu)
        self.progress_action.setDefaultWidget(self.progress_view)
        self.progress_action.setVisible(False)
        self.menu.addAction(self.progress_action)
        # opens the window; enabled, macOS draws it at full contrast instead of dimmed
        self.status_action = self._add("Ready", window.show_window)
        self.cancel_action = self._add("Cancel", lambda: (window.show_window(), window.cancel_job()))
        self.menu.addSeparator()
        self._add("Open Vaquero", window.show_window)
        self.scan_action = self._add("Scan", lambda: (window.show_window(), window.start_scan()))
        self.undo_action = self._add("Undo", window.undo_last)
        self.menu.addSeparator()
        self.dock_action = self._add("Show in Dock", window.toggle_dock)
        self.dock_action.setCheckable(True)
        self._add("Settings…", lambda: (window.show_window(), window.show_settings()))
        self._add("Check for Updates…", lambda: (window.show_window(), window.check_updates()))
        self._add("Open Log", window.open_app_log)
        self._add("Vaquero Help", lambda: (window.show_window(), window.show_help()))
        self.menu.addSeparator()
        self._add("Quit Vaquero", window.quit_app)
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self.refresh)

        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._tick)

    def _add(self, text: str, slot: Callable[[], object] | None = None, enabled: bool = True) -> QAction:
        action = self.menu.addAction(text)
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(lambda _=False: slot())
        return action

    @property
    def working(self) -> bool:
        return self.timer.isActive()

    def _draw_frames(self, dark: bool) -> None:
        """The sweep, then the glyph held still, in color: white on a dark menu bar, black on a light one."""
        glyph = QColor(255, 255, 255, 235) if dark else QColor(0, 0, 0, 215)
        steps = [i / (SWEEP_FRAMES - 1) for i in range(SWEEP_FRAMES)]
        sweep = [color_icon([_frame(b, glyph, f) for b in self.bases]) for f in steps]
        hold = color_icon([_frame(b, glyph, None) for b in self.bases])
        self.frames = sweep + [hold] * HOLD_FRAMES
        self.dark = dark

    def _match_menu_bar(self) -> None:
        """Redraw the working frames if the menu bar turned light or dark (on macOS 26 and later it follows the
        wallpaper)."""
        dark = macos.menu_bar_dark()
        if dark is None:
            dark = QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        if dark != self.dark:
            self._draw_frames(dark)

    def set_working(self, on: bool) -> None:
        if on and not self.timer.isActive():
            self._match_menu_bar()
            self.frame = 0
            self.timer.start()
        elif not on and self.timer.isActive():
            self.timer.stop()
            self.tray.setIcon(self.still)
        self.refresh()

    def _tick(self) -> None:
        self.frame = (self.frame + 1) % len(self.frames)
        if self.frame == 0:  # each sweep starts matched to the menu bar as it is now
            self._match_menu_bar()
        self.tray.setIcon(self.frames[self.frame])
        if self.frame % 2 == 0:  # the open menu follows the job four times a second
            self.refresh()

    def refresh(self) -> None:
        """Bring the menu's lines up to date with the window: status, progress, and what can be done now."""
        w = self.window
        # busy before its progress starts (checking folders, loading references): what the window says; idle: the
        # last result, short
        status = shorten(w.status_plain()) if w.busy else w.menu_status
        self.status_action.setText(status)
        state = w.progress_state if w.busy else None
        # while a job runs, its progress takes the status line's place
        self.progress_action.setVisible(state is not None)
        self.status_action.setVisible(state is None)
        if state is not None:
            label, done, total, left = state
            self.progress_view.show_state(fraction(done, total), progress_text(label, done, total), left, menu_is_dark())
            pct = f" {done * 100 // total}%" if total > 0 else ""
            self.tray.setToolTip(f"Vaquero: {label}{pct}")
        else:
            self.tray.setToolTip(f"Vaquero: {status}" if status else "Vaquero")
        self.cancel_action.setEnabled(w.busy)
        self.scan_action.setEnabled(not w.busy and bool(w.packs))
        undo = w.undo_text()
        self.undo_action.setText(undo or "Undo")
        self.undo_action.setEnabled(bool(undo) and not w.busy)
        self.dock_action.setChecked(w.dock_shown())
        self.dock_action.setEnabled(self.tray.isVisible())  # turning the Dock off needs the menu bar icon
