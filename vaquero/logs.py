"""The app log, crash capture, and plain-language messages for errors.

Everything goes to ~/Library/Logs/Vaquero: vaquero.log (rotating, from launch on, everything the window shows plus
tracebacks), vaquero-crash.log (native crashes in onnxruntime, OpenCV or Qt), and one log and CSV report per scan."""

from __future__ import annotations

import errno
import faulthandler
import logging
import os
import socket
import sys
import threading
import urllib.error
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import TextIO

from vaquero.remote import LOG_DIR, RemoteError

log = logging.getLogger("vaquero")

APP_LOG = LOG_DIR / "vaquero.log"
CRASH_LOG = LOG_DIR / "vaquero-crash.log"
APP_LOG_BYTES, APP_LOG_BACKUPS = 2 << 20, 3
SCANS_KEPT = 50  # per-scan logs and reports beyond the newest this many are removed at launch
FORMAT = "%(asctime)s %(levelname)s %(message)s"

_crash_file: TextIO | None = None  # held open for faulthandler, which writes to it from a signal handler


class FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "section", False):
            return f"\n== {record.getMessage()} =="
        return super().format(record)


def start_app_log() -> Path:
    """Log to vaquero.log from launch: model download and update failures happen before any scan log exists."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(APP_LOG, maxBytes=APP_LOG_BYTES, backupCount=APP_LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(FileFormatter(FORMAT))
    log.addHandler(handler)
    return APP_LOG


def prune_scan_logs(keep: int = SCANS_KEPT) -> int:
    """Remove all but the newest `keep` scan logs and scan reports; returns how many files went."""
    removed = 0
    for pattern in ("vaquero-[0-9]*.log", "vaquero-scan-*.csv"):
        files = sorted(LOG_DIR.glob(pattern), key=lambda p: p.name, reverse=True)  # names hold the timestamp
        for old in files[keep:]:
            try:
                old.unlink()
                removed += 1
            except OSError as e:
                log.warning("could not remove old log %s: %s", old, e)
    return removed


def _log_uncaught(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, value, tb)
        return
    log.critical("unexpected error: %s", describe(value), exc_info=(kind, value, tb))


def _log_uncaught_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None:
        _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)


def install_crash_hooks() -> None:
    """Send every uncaught Python exception, on any thread, to the log with its traceback, and native crashes to
    vaquero-crash.log. Without this they go to stderr, which an app bundle discards."""
    global _crash_file
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_thread
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _crash_file = CRASH_LOG.open("a", encoding="utf-8")
    faulthandler.enable(file=_crash_file, all_threads=True)


def volume_of(path: Path) -> Path | None:
    """/Volumes/<name> for a path on an external drive or share, None for the startup disk."""
    parts = path.expanduser().parts
    return Path(*parts[:3]) if len(parts) >= 3 and parts[1] == "Volumes" else None


def missing_volume(path: Path) -> str | None:
    """The name of the drive or share path lives on when it is not connected, else None."""
    vol = volume_of(path)
    if vol is None or os.path.ismount(vol):
        return None
    return vol.name


NETWORK_ERRNOS = frozenset({errno.ETIMEDOUT, errno.EHOSTDOWN, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ENETUNREACH,
                            errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED})


def describe(e: BaseException) -> str:
    """One plain sentence for the status line. The log keeps the exception itself."""
    if isinstance(e, RemoteError):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        return f"The server answered {e.code} {e.reason}. Try again later."
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            return "No internet connection."
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "The server did not answer in time. Try again."
        return f"Could not reach the server: {reason}."
    if isinstance(e, TimeoutError):
        return "The connection timed out. Try again."
    if isinstance(e, OSError):
        where = Path(e.filename) if e.filename else None
        if where is not None and (drive := missing_volume(where)):
            return f"The drive {drive} is not connected. Connect it and try again."
        shown = str(where) if where else "the file"
        if e.errno == errno.EPERM:
            return (
                f"macOS blocked access to {shown}. Allow Vaquero in System Settings > Privacy & Security > "
                "Files and Folders (Removable Volumes, Network Volumes) or Full Disk Access, then try again."
            )
        if e.errno == errno.EACCES:
            return f"No permission to use {shown}. Check its Sharing & Permissions in Finder > Get Info."
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            return f"Not enough space for {shown}. Free some space or pick another destination."
        if e.errno == errno.EROFS:
            return f"{shown} is on a read-only drive. Pick another destination."
        if e.errno == errno.ENOENT:
            return f"{shown} is no longer there."
        if e.errno in NETWORK_ERRNOS or e.errno == errno.EIO:
            return f"Lost the connection to {shown}. Check the drive or network share and try again."
        return f"{e.strerror or e} ({shown})." if e.strerror else f"{e}."
    if isinstance(e, MemoryError):
        return "Ran out of memory. Close other apps, or scan fewer frames per video."
    return f"Something went wrong: {e}. The details are in the log." if str(e) else "Something went wrong. The details are in the log."


def shorten(text: str, limit: int = 44) -> str:
    """text cut at a word to at most limit characters, for the menu bar menu, whose lines never wrap."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:·")
    return f"{cut}…"
