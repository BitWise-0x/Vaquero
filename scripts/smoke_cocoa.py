"""Opens Vaquero's window on the real macOS window system (not offscreen, as the tests run), with throwaway
settings, and checks the parts only cocoa has: the menu bar icon, the Dock icon switched off and on, the window
closing into the menu bar, the icon animating during a job, and quitting. Exits 1 on the first failure.

Run: QT_QPA_PLATFORM=cocoa uv run python scripts/smoke_cocoa.py
"""

import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from vaquero import app as ui
from vaquero import macos

failures: list[str] = []


def check(name: str, ok: bool) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}", flush=True)
    if not ok:
        failures.append(name)


def main() -> int:
    ini = Path(tempfile.mkdtemp()) / "vaquero.ini"
    ui.QSettings = lambda *_: QSettings(str(ini), QSettings.Format.IniFormat)
    qa = QApplication([])
    qa.setQuitOnLastWindowClosed(False)
    check("cocoa platform", qa.platformName() == "cocoa")
    w = ui.Window()
    qa.installEventFilter(w)
    w.apply_presence()
    w.show()

    def first() -> None:
        check("menu bar available", QSystemTrayIcon.isSystemTrayAvailable())
        check("menu bar icon shown", w.status_item.tray.isVisible())
        check("Dock icon shown", macos.dock_visible())
        w.close()
        check("closing keeps Vaquero in the menu bar", not w.isVisible())
        w.set_presence(dock=False, menu_bar=True)
        check("Dock icon off", not macos.dock_visible())
        w.set_presence(dock=True, menu_bar=True)
        check("Dock icon back", macos.dock_visible())
        w._run(ui.TaskWorker(lambda progress, cancelled: time.sleep(1)), "a scan")
        check("icon animates during a job", w.status_item.working)
        QTimer.singleShot(2000, second)

    def second() -> None:
        check("icon still after the job", not w.status_item.working)
        w.show_window()
        check("window back", w.isVisible())
        w.quit_app()

    QTimer.singleShot(500, first)
    QTimer.singleShot(15000, lambda: (failures.append("timed out"), qa.exit(1)))
    qa.exec()
    print("smoke test", "failed: " + ", ".join(failures) if failures else "passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
