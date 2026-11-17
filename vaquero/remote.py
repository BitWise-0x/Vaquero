"""Where Vaquero keeps its files, and what it fetches from its GitHub repository (models, app updates).

The repository is private, so downloads go through the GitHub CLI and the account it is logged in with.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("vaquero")

REPO = "BitWise-0x/vaquero"
MODELS_TAG = "models-buffalo_l"
MODELS_ASSET = "buffalo_l.zip"
DEFAULT_PACK = "buffalo_l"

LOG_DIR = Path.home() / "Library" / "Logs" / "Vaquero"
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Vaquero"
MODELS_DIR = SUPPORT_DIR / "models"
# partial downloads live here, outside any temp folder, so quitting mid-download resumes on the next launch
DOWNLOADS_DIR = SUPPORT_DIR / "downloads"
JOURNAL = SUPPORT_DIR / "vaquero-moves.csv"
# the app's earlier name: its folders, journal and settings carry over on first launch
LEGACY_NAME = "FaceSort"
LEGACY_JOURNAL = "facesort-moves.csv"
CHUNK = 1 << 20

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
class Asset:
    name: str
    size: int
    api_url: str
    digest: str  # "sha256:<hex>" as GitHub reports it, empty when it has none


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, ...]
    assets: dict[str, Asset]


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
    assets = {
        a["name"]: Asset(a["name"], int(a["size"]), a["apiUrl"], a.get("digest") or "") for a in data["assets"]
    }
    version = parse_version(data["tagName"]) if data["tagName"].startswith("v") else ()
    return Release(tag=data["tagName"], version=version, assets=assets)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _storage_url(asset: Asset) -> str:
    """The signed storage URL GitHub redirects an asset to, fetched with the gh login's token.

    The token goes only to api.github.com; the signed URL carries its own authorization."""
    token = _run("auth", "token").strip()
    req = urllib.request.Request(
        asset.api_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"}
    )
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and (location := e.headers.get("Location")):
            return location
        raise RemoteError(f"GitHub refused {asset.name}: HTTP {e.code}") from e
    raise RemoteError(f"GitHub did not redirect {asset.name} to storage")


def download(
    asset: Asset,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Download an asset into DOWNLOADS_DIR, resuming a partial download left by an earlier run.

    Cancelling keeps the partial file. A finished file is checked against GitHub's digest."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS_DIR / asset.name
    part = target.with_name(target.name + ".part")
    if target.exists() and target.stat().st_size == asset.size and _digest_ok(target, asset):
        progress(asset.size, asset.size)
        return target
    have = part.stat().st_size if part.exists() else 0
    if have > asset.size:
        part.unlink()
        have = 0
    if have < asset.size:
        req = urllib.request.Request(_storage_url(asset), headers={"Range": f"bytes={have}-"} if have else {})
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.URLError as e:
            raise RemoteError(f"download of {asset.name} failed: {e}") from e
        with resp:
            if have and resp.status != 206:
                have = 0  # the server ignored the range: start over
            if have:
                log.info("resuming %s at %.0f of %.0f MB", asset.name, have / 1e6, asset.size / 1e6)
            with part.open("ab" if have else "wb") as fh:
                while chunk := resp.read(CHUNK):
                    fh.write(chunk)
                    have += len(chunk)
                    progress(have, asset.size)
                    if cancelled():
                        raise RemoteError(f"download paused at {have / 1e6:.0f} MB; it resumes next time")
    if part.stat().st_size != asset.size:
        raise RemoteError(f"{asset.name} is incomplete; it resumes next time")
    if not _digest_ok(part, asset):
        part.unlink()
        raise RemoteError(f"{asset.name} did not match its checksum and was discarded; try again")
    part.replace(target)
    progress(asset.size, asset.size)
    return target


def _digest_ok(path: Path, asset: Asset) -> bool:
    algo, _, expected = asset.digest.partition(":")
    if algo != "sha256" or not expected:
        return True
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest() == expected


def ensure_models(progress: Callable[[int, int], None], cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download and unpack the default model pack into MODELS_DIR unless it is already there."""
    from vaquero.engine import pack_models

    pack = MODELS_DIR / DEFAULT_PACK
    if pack.is_dir() and pack_models(pack):
        return pack
    rel = release(MODELS_TAG)
    if MODELS_ASSET not in rel.assets:
        raise RemoteError(f"release {MODELS_TAG} has no {MODELS_ASSET}")
    archive = download(rel.assets[MODELS_ASSET], progress, cancelled)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SUPPORT_DIR) as tmp:
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
    archive.unlink()
    log.info("model pack %s installed in %s", DEFAULT_PACK, pack)
    return pack


def migrate_legacy() -> None:
    """Move the FaceSort-era logs, support folder (models, downloads) and undo journal to their Vaquero names, once."""
    for new in (LOG_DIR, SUPPORT_DIR):
        old = new.with_name(LEGACY_NAME)
        if old.is_dir() and not new.exists():
            try:
                old.rename(new)
            except OSError as e:
                log.warning("could not move %s to %s: %s", old, new, e)
                continue
            log.info("moved %s to %s", old, new)
    old_journal = SUPPORT_DIR / LEGACY_JOURNAL
    if old_journal.exists() and not JOURNAL.exists():
        try:
            old_journal.rename(JOURNAL)
        except OSError as e:
            log.warning("could not rename %s to %s: %s", old_journal, JOURNAL.name, e)


def running_bundle() -> Path | None:
    """The .app this process runs from, None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    return next((p for p in Path(sys.executable).resolve().parents if p.suffix == ".app"), None)


def stage_update(dmg: Path, workdir: Path) -> Path:
    """Copy Vaquero.app out of a downloaded DMG into workdir; returns the staged bundle."""
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
