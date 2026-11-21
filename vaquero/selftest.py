"""`Vaquero --self-test`: the app checks itself on the real macOS window system, with throwaway settings, and exits
0 or 1. Run against the built bundle, it covers what only the bundle has: its assets, its native libraries (ONNX
Runtime with Core ML, OpenCV, HEIF) and its signature; from source, the menu bar and Dock wiring. Nothing is read
from or written to the user's settings, folders or logs."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

failures: list[str] = []


def check(name: str, ok: bool) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}", flush=True)
    if not ok:
        failures.append(name)


def _libraries() -> None:
    import cv2
    import numpy as np
    import onnxruntime as ort
    import pi_heif

    check("ONNX Runtime with Core ML", "CoreMLExecutionProvider" in ort.get_available_providers())
    ok, encoded = cv2.imencode(".png", np.zeros((4, 4, 3), np.uint8))
    check("OpenCV encodes and decodes", ok and cv2.imdecode(encoded, cv2.IMREAD_COLOR) is not None)
    check("HEIF support", bool(pi_heif.libheif_version()))


def run() -> NoReturn:
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from vaquero import app as ui
    from vaquero import macos, remote

    scratch = tempfile.TemporaryDirectory()  # the throwaway settings; removed before the test exits
    ini = Path(scratch.name) / "vaquero.ini"
    ui.QSettings = lambda *_: QSettings(str(ini), QSettings.Format.IniFormat)  # type: ignore[assignment]
    qa = QApplication(sys.argv[:1])
    qa.setQuitOnLastWindowClosed(False)
    check("cocoa platform", qa.platformName() == "cocoa")
    for name in ("Vaquero.png", "buffalo.png", "check.svg", "MenuBar-18.png", "MenuBar-36.png"):
        check(f"asset {name}", ui.asset(name).exists() and (name.endswith(".svg") or not QPixmap(str(ui.asset(name))).isNull()))
    _libraries()
    bundle = remote.running_bundle()
    if bundle is not None:
        check("running bundle found", bundle.suffix == ".app")
        check("login item service answers", macos.login_item_status() in range(4))

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
        activity = w.activity is not None and w.activity.active
        check("Mac kept awake during a job", activity)
        QTimer.singleShot(2000, second)

    def second() -> None:
        check("icon still after the job", not w.status_item.working)
        check("Mac free to sleep after the job", w.activity is None)
        w.show_window()
        check("window back", w.isVisible())
        w.quit_app()

    QTimer.singleShot(500, first)
    QTimer.singleShot(20000, lambda: (failures.append("timed out"), qa.exit(1)))
    qa.exec()
    print("self-test", "failed: " + ", ".join(failures) if failures else "passed", flush=True)
    scratch.cleanup()
    # end here: Qt would write the throwaway settings again while tearing down, recreating the folder just removed
    os._exit(1 if failures else 0)
