"""What macOS lets Vaquero reach, and where Vaquero itself lives: Full Disk Access, the state of each chosen folder,
and moving the app into Applications when it runs from somewhere it cannot stay (a disk image, Downloads, or the
temporary copy macOS makes of an app opened straight from Downloads)."""

from __future__ import annotations

import errno
import logging
import os
import subprocess
from enum import StrEnum
from pathlib import Path

from vaquero import macos

log = logging.getLogger("vaquero")

PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"
FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
# folders macOS lets an app list only with Full Disk Access; the first that exists decides
_FDA_PROBES = tuple(Path.home() / "Library" / name for name in ("Safari", "Mail", "Messages", "Application Support/com.apple.TCC"))


def full_disk_access() -> bool | None:
    """Whether Vaquero has Full Disk Access; None when it cannot be told."""
    for probe in _FDA_PROBES:
        try:
            with os.scandir(probe) as entries:
                next(entries, None)
            return True
        except PermissionError:
            return False
        except OSError:
            continue
    return None


class FolderState(StrEnum):
    OK = "OK"
    BLOCKED = "Needs your permission"
    NOT_CONNECTED = "Drive not connected"
    MISSING = "Not found"
    NO_PERMISSION = "No permission"
    NOT_SET = "Not chosen"


def folder_state(path_text: str) -> FolderState:
    """One chosen folder as the Settings window reports it. May block on a slow share: call it off the main thread."""
    from vaquero.logs import missing_volume

    if not path_text.strip():
        return FolderState.NOT_SET
    path = Path(path_text.strip()).expanduser()
    if missing_volume(path):
        return FolderState.NOT_CONNECTED
    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except PermissionError as e:
        return FolderState.BLOCKED if e.errno == errno.EPERM else FolderState.NO_PERMISSION
    except FileNotFoundError:
        return FolderState.MISSING
    except OSError:
        return FolderState.MISSING
    return FolderState.OK


def move_offer_reason(bundle: Path) -> str | None:
    """Why the app should be moved into Applications before it is used, or None when it is fine where it is."""
    text = str(bundle)
    if "/AppTranslocation/" in text:
        return "Vaquero is running from a temporary copy made by macOS."
    if text.startswith("/Volumes/") and not os.access(bundle.parent, os.W_OK):
        return "Vaquero is running from its disk image."
    if bundle.parent == Path.home() / "Downloads":
        return "Vaquero is in the Downloads folder."
    return None


def applications_folder() -> Path:
    """/Applications when this account can write there, else ~/Applications."""
    system = Path("/Applications")
    return system if os.access(system, os.W_OK) else Path.home() / "Applications"


def move_to_applications(bundle: Path) -> Path:
    """Copy the app into Applications (an older copy there goes to the Trash), clear the quarantine flag the
    download left, and return the new location. The copy is made with ditto, which keeps the code signature."""
    target_dir = applications_folder()
    target_dir.mkdir(exist_ok=True)
    target = target_dir / bundle.name
    if target.exists():
        if not macos.trash(str(target)):
            raise OSError(errno.EEXIST, "an older Vaquero is in the way and could not go to the Trash", str(target))
    subprocess.run(["ditto", str(bundle), str(target)], check=True, capture_output=True, text=True)
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(target)], capture_output=True)
    log.info("copied Vaquero to %s", target, extra={"link": target, "link_text": str(target)})
    return target
