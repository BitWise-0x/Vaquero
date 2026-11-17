"""Where FaceSort keeps its files, and what it fetches from its GitHub repository (models, app updates).

The repository is private, so downloads go through the GitHub CLI and the account it is logged in with.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("facesort")

REPO = "BitWise-0x/facesort"
MODELS_TAG = "models-buffalo_l"
MODELS_ASSET = "buffalo_l.zip"
DEFAULT_PACK = "buffalo_l"

LOG_DIR = Path.home() / "Library" / "Logs" / "FaceSort"
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "FaceSort"
MODELS_DIR = SUPPORT_DIR / "models"

# a Finder-launched app gets a bare PATH without Homebrew
GH_CANDIDATES = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")


class RemoteError(RuntimeError):
    pass


def gh() -> str:
    found = shutil.which("gh") or next((c for c in GH_CANDIDATES if Path(c).is_file()), None)
    if not found:
        raise RemoteError("GitHub CLI not found. Install it (brew install gh) and run: gh auth login")
    return found


def _run(*args: str, timeout: float = 60) -> str:
    try:
        r = subprocess.run([gh(), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RemoteError(f"gh {args[0]} timed out") from e
    if r.returncode != 0:
        raise RemoteError(r.stderr.strip() or f"gh {args[0]} failed with exit code {r.returncode}")
    return r.stdout


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, ...]
    assets: dict[str, int]  # name -> size in bytes


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.4.1' -> (0, 4, 1); anything after a '-' (a prerelease) is ignored."""
    core = text.lstrip("v").split("-", 1)[0]
    try:
        return tuple(int(p) for p in core.split("."))
    except ValueError as e:
        raise RemoteError(f"not a version: {text}") from e


def release(tag: str | None = None) -> Release:
    """The named release, or the latest app release when tag is None."""
    args = ["release", "view", *([tag] if tag else []), "--repo", REPO, "--json", "tagName,assets"]
    data = json.loads(_run(*args))
    assets = {a["name"]: int(a["size"]) for a in data["assets"]}
    version = parse_version(data["tagName"]) if data["tagName"].startswith("v") else ()
    return Release(tag=data["tagName"], version=version, assets=assets)


def download(
    tag: str,
    asset: str,
    size: int,
    dest_dir: Path,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Download one release asset into dest_dir, reporting bytes received as it goes."""
    target = dest_dir / asset
    proc = subprocess.Popen(
        [gh(), "release", "download", tag, "--repo", REPO, "--pattern", asset, "--dir", str(dest_dir), "--clobber"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr: list[str] = []
    reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancelled():
            proc.terminate()
            proc.wait()
            raise RemoteError("download cancelled")
        progress(target.stat().st_size if target.exists() else 0, size)
        time.sleep(0.3)
    reader.join(5)
    if proc.returncode != 0:
        raise RemoteError("".join(stderr).strip() or f"download of {asset} failed")
    progress(size, size)
    return target


def ensure_models(progress: Callable[[int, int], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download and unpack the default model pack into MODELS_DIR unless it is already there."""
    from facesort.engine import pack_models

    pack = MODELS_DIR / DEFAULT_PACK
    if pack.is_dir() and pack_models(pack):
        return pack
    rel = release(MODELS_TAG)
    if MODELS_ASSET not in rel.assets:
        raise RemoteError(f"release {MODELS_TAG} has no {MODELS_ASSET}")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SUPPORT_DIR) as tmp:
        archive = download(MODELS_TAG, MODELS_ASSET, rel.assets[MODELS_ASSET], Path(tmp), progress, cancelled)
        staging = Path(tmp) / DEFAULT_PACK
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                name = Path(member).name
                if name.endswith(".onnx"):
                    staging.mkdir(exist_ok=True)
                    (staging / name).write_bytes(z.read(member))
        if not pack_models(staging):
            raise RemoteError(f"{MODELS_ASSET} does not hold a detector and recognizer")
        if pack.exists():
            shutil.rmtree(pack)
        staging.rename(pack)
    log.info("model pack %s installed in %s", DEFAULT_PACK, pack)
    return pack


def running_bundle() -> Path | None:
    """The .app this process runs from, None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    return next((p for p in Path(sys.executable).resolve().parents if p.suffix == ".app"), None)


def stage_update(dmg: Path, workdir: Path) -> Path:
    """Copy FaceSort.app out of a downloaded DMG into workdir; returns the staged bundle."""
    mount = workdir / "mnt"
    mount.mkdir()
    subprocess.run(
        ["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mount)],
        check=True,
        capture_output=True,
    )
    try:
        apps = list(mount.glob("*.app"))
        if len(apps) != 1:
            raise RemoteError(f"expected one app in the update image, found {len(apps)}")
        staged = workdir / apps[0].name
        subprocess.run(["ditto", str(apps[0]), str(staged)], check=True, capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], capture_output=True)
    return staged


def install_and_relaunch(staged: Path, bundle: Path, pid: int) -> None:
    """Replace bundle with staged once this process (pid) has exited, then reopen it.

    A detached shell does the swap, since a running app cannot replace its own bundle."""
    script = staged.parent / "install.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'while kill -0 {pid} 2>/dev/null; do sleep 0.5; done\n'
        f'rm -rf "{bundle}.old"\n'
        # put the old app back if the new one cannot be moved in
        f'if mv "{bundle}" "{bundle}.old"; then\n'
        f'  if mv "{staged}" "{bundle}"; then rm -rf "{bundle}.old"; else mv "{bundle}.old" "{bundle}"; fi\n'
        "fi\n"
        f'xattr -dr com.apple.quarantine "{bundle}" 2>/dev/null\n'
        f'open "{bundle}"\n'
        f'rm -rf "{staged.parent}"\n'
    )
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True)
