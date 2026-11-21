import errno
import logging
import socket
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from vaquero import logs
from vaquero.remote import RemoteError


def test_privacy_block_says_where_to_allow_access() -> None:
    msg = logs.describe(PermissionError(errno.EPERM, "Operation not permitted", "/Users/you/Pictures/faces"))
    assert "/Users/you/Pictures/faces" in msg and "Privacy & Security" in msg


def test_file_permissions_point_to_get_info_not_privacy() -> None:
    msg = logs.describe(PermissionError(errno.EACCES, "Permission denied", "/Users/you/shared"))
    assert "Get Info" in msg and "Privacy" not in msg


def test_a_path_on_a_disconnected_drive_names_the_drive() -> None:
    assert logs.missing_volume(Path("/Volumes/NoSuchDriveXYZ/photos")) == "NoSuchDriveXYZ"
    assert logs.missing_volume(Path("/Users/you/photos")) is None
    assert logs.missing_volume(Path("/Volumes")) is None
    msg = logs.describe(PermissionError(errno.EACCES, "Permission denied", "/Volumes/NoSuchDriveXYZ/out"))
    assert msg == "The drive NoSuchDriveXYZ is not connected. Connect it and try again."


@pytest.mark.parametrize(
    ("error", "expect"),
    [
        (OSError(errno.ENOSPC, "No space left on device", "/Users/you/out/a.jpg"), "Not enough space"),
        (OSError(errno.EROFS, "Read-only file system", "/Users/you/out"), "read-only"),
        (FileNotFoundError(errno.ENOENT, "No such file", "/Users/you/a.jpg"), "no longer there"),
        (OSError(errno.EIO, "Input/output error", "/Users/you/a.jpg"), "Lost the connection"),
        (OSError(errno.EHOSTDOWN, "Host is down", "/Users/you/a.jpg"), "Lost the connection"),
        (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), "No internet connection"),
        (urllib.error.URLError(TimeoutError("timed out")), "did not answer in time"),
        (TimeoutError("timed out"), "timed out"),
        (RemoteError("GitHub's hourly limit is used up"), "GitHub's hourly limit is used up"),
        (MemoryError(), "memory"),
        (KeyError("tag_name"), "Something went wrong"),
    ],
)
def test_errors_read_as_plain_sentences(error: BaseException, expect: str) -> None:
    msg = logs.describe(error)
    assert expect in msg
    assert "Errno" not in msg and "<urlopen" not in msg


def test_prune_keeps_the_newest_scans_and_the_app_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    for day in range(1, 8):
        (tmp_path / f"vaquero-202611{day:02d}-120000.log").write_text("x")
        (tmp_path / f"vaquero-scan-202611{day:02d}-120000.csv").write_text("x")
    (tmp_path / "vaquero.log").write_text("app")
    (tmp_path / "vaquero.log.1").write_text("app")
    (tmp_path / "vaquero-crash.log").write_text("crash")

    assert logs.prune_scan_logs(keep=3) == 8

    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [
        "vaquero-20261105-120000.log", "vaquero-20261106-120000.log", "vaquero-20261107-120000.log",
        "vaquero-crash.log",
        "vaquero-scan-20261105-120000.csv", "vaquero-scan-20261106-120000.csv", "vaquero-scan-20261107-120000.csv",
        "vaquero.log", "vaquero.log.1",
    ]


@pytest.fixture
def hooked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logs, "APP_LOG", tmp_path / "vaquero.log")
    monkeypatch.setattr(logs, "CRASH_LOG", tmp_path / "vaquero-crash.log")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    logger = logging.getLogger("vaquero")
    before = list(logger.handlers)
    logger.setLevel(logging.INFO)
    logs.install_crash_hooks()
    logs.start_app_log()
    yield tmp_path
    for h in logger.handlers[len(before):]:
        h.close()
        logger.removeHandler(h)
    import faulthandler

    faulthandler.disable()


def test_uncaught_exceptions_reach_the_app_log_with_a_traceback(hooked: Path) -> None:
    try:
        raise ValueError("boom in a slot")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    t = threading.Thread(target=lambda: 1 / 0)
    t.start()
    t.join()

    text = (hooked / "vaquero.log").read_text()
    assert "CRITICAL unexpected error: Something went wrong: boom in a slot" in text
    assert "Traceback" in text and "ZeroDivisionError" in text
    assert (hooked / "vaquero-crash.log").exists()
