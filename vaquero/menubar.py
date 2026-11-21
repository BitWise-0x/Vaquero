"""Vaquero's menu bar icon and its menu: what is running, how far along, and the actions that need no window.

The icon is a template image (macOS tints it for a light or dark menu bar). It holds still while Vaquero is idle,
and the scan line sweeps across it while a scan, copy, move or download runs, as it does in the window header."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRectF, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

if TYPE_CHECKING:
    from vaquero.app import Window

SIZES = (18, 36)  # 18 pt at 1x and 2x
SWEEP_FRAMES, HOLD_FRAMES, FRAME_MS = 16, 8, 125  # a 2 s sweep, then 1 s still, at 8 frames a second
INSET = 0.14  # the line stays inside the viewfinder corners, as in the window header
BAR_CELLS = 12


def _frame(base: QPixmap, progress: float) -> QPixmap:
    """base with the scan line at progress (0 top, 1 bottom). Drawn with XOR, so the line shows as a gap across the
    buffalo and as a stroke across the empty parts: a template image has only one color."""
    pix = base.copy()
    edge = pix.width()
    lo, hi = edge * INSET, edge * (1 - INSET)
    y = lo + progress * (hi - lo)
    thick = max(1.0, edge / 18)
    p = QPainter(pix)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Xor)
    p.fillRect(QRectF(lo, y - thick / 2, hi - lo, thick), QColor(0, 0, 0))
    p.end()
    return pix


def template_icon(pixmaps: list[QPixmap]) -> QIcon:
    icon = QIcon()
    for pix in pixmaps:
        icon.addPixmap(pix)
    icon.setIsMask(True)
    return icon


def progress_text(label: str, done: int, total: int, detail: str) -> str:
    """One menu line: a bar of block characters, the percentage and the detail."""
    frac = min(done / total, 1.0) if total > 0 else 0.0
    filled = round(frac * BAR_CELLS)
    return f"{'▮' * filled}{'▯' * (BAR_CELLS - filled)}  {frac:.0%}  {label}: {detail}"


class StatusItem(QObject):
    def __init__(self, window: Window, asset: Callable[[str], object]) -> None:
        super().__init__(window)
        self.window = window
        bases = [QPixmap(str(asset(f"MenuBar-{size}.png"))) for size in SIZES]
        self.still = template_icon(bases)
        frames = [i / (SWEEP_FRAMES - 1) for i in range(SWEEP_FRAMES)]
        self.frames = [template_icon([_frame(b, f) for b in bases]) for f in frames] + [self.still] * HOLD_FRAMES
        self.frame = 0

        self.tray = QSystemTrayIcon(self.still, self)
        self.tray.setToolTip("Vaquero")
        self.menu = QMenu()
        self.status_action = self._add("Ready", enabled=False)
        self.progress_action = self._add("", enabled=False)
        self.progress_action.setVisible(False)
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

    def set_working(self, on: bool) -> None:
        if on and not self.timer.isActive():
            self.frame = 0
            self.timer.start()
        elif not on and self.timer.isActive():
            self.timer.stop()
            self.tray.setIcon(self.still)
        self.refresh()

    def _tick(self) -> None:
        self.frame = (self.frame + 1) % len(self.frames)
        self.tray.setIcon(self.frames[self.frame])
        if self.frame % 2 == 0:  # the open menu follows the job four times a second
            self.refresh()

    def refresh(self) -> None:
        """Bring the menu's lines up to date with the window: status, progress, and what can be done now."""
        w = self.window
        status = w.status_plain()
        self.status_action.setText(status if len(status) <= 70 else status[:69] + "…")
        state = w.progress_state if w.busy else None
        self.progress_action.setVisible(state is not None)
        if state is not None:
            self.progress_action.setText(progress_text(*state))
            label, done, total, _ = state
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
