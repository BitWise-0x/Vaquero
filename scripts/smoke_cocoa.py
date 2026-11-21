"""Runs Vaquero's self-test from source (see vaquero/selftest.py): the menu bar and Dock wiring on the real macOS
window system, with throwaway settings. The release build runs the same test inside the built app.

Run: QT_QPA_PLATFORM=cocoa uv run python scripts/smoke_cocoa.py
"""

import sys

from vaquero.selftest import run

sys.exit(run())
