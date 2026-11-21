"""Where Vaquero keeps its files, and what it fetches from its public GitHub repository (models, app updates)."""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import plistlib
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("vaquero")

REPO = "BitWise-0x/Vaquero"
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

API = f"https://api.github.com/repos/{REPO}/releases"
USER_AGENT = "Vaquero"


class RemoteError(RuntimeError):
    pass


class DownloadPaused(RemoteError):
    """The user stopped a download; the partial file stays and the next attempt resumes it."""


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    url: str  # public download URL
    digest: str  # "sha256:<hex>" as GitHub reports it, empty when it has none
    mirrors: tuple[str, ...] = ()  # the same file elsewhere, tried in order when url fails


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, ...]
    assets: dict[str, Asset]


# the face model: InsightFace's buffalo_l pack, unmodified, from this repo's own model release (published with
# scripts/publish_model.sh), pinned to this exact file; InsightFace's releases of the same bytes are the fallbacks
MODEL_ARCHIVE = Asset(
    name="buffalo_l.zip",
    size=288_621_354,
    url=f"https://github.com/{REPO}/releases/download/model-buffalo_l-1/buffalo_l.zip",
    digest="sha256:80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
    mirrors=(
        "https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip",
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
    ),
)
# the archive also holds age, gender and landmark models that Vaquero does not use; name: (size, sha256)
MODEL_FILES = {
    "det_10g.onnx": (16_923_827, "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"),
    "w600k_r50.onnx": (174_383_860, "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43"),
}


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.4.1' -> (0, 4, 1); anything after a '-' (a prerelease) is ignored."""
    core = text.lstrip("v").split("-", 1)[0]
    try:
        return tuple(int(p) for p in core.split("."))
    except ValueError as e:
        raise RemoteError(f"not a version: {text}") from e


def _rate_limit_message(e: urllib.error.HTTPError) -> str:
    reset = e.headers.get("x-ratelimit-reset") if e.headers else None
    if reset and reset.isdigit():
        at = datetime.fromtimestamp(int(reset)).strftime("%H:%M")
        return f"GitHub's hourly limit for anonymous requests is used up; try again after {at}"
    return "GitHub's hourly limit for anonymous requests is used up; try again later"


def _get_json(url: str) -> object:
    """GET a GitHub API URL. Every failure, including a captive Wi-Fi login page answering instead of GitHub,
    becomes a RemoteError with a readable message."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RemoteError(f"GitHub has no such release: {url}") from e
        if e.code in (403, 429):
            raise RemoteError(_rate_limit_message(e)) from e
        raise RemoteError(f"GitHub answered HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise RemoteError(f"could not reach GitHub: {e.reason}") from e
    except (http.client.HTTPException, ValueError) as e:  # cut off mid-answer, or not JSON (a login page)
        raise RemoteError(
            "GitHub's answer was cut off or unreadable; a Wi-Fi login page can cause this. Try again."
        ) from e
    except OSError as e:  # a reset or timeout while reading the body
        raise RemoteError(f"could not reach GitHub: {e}") from e


def _parse_release(data: object) -> Release:
    unexpected = RemoteError("GitHub's release information was not in the expected form")
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list) or "tag_name" not in data:
        raise unexpected
    tag_name = str(data["tag_name"])
    try:
        assets = {
            a["name"]: Asset(a["name"], int(a["size"]), a["browser_download_url"], a.get("digest") or "")
            for a in data["assets"]
        }
    except (KeyError, TypeError, ValueError) as e:
        raise unexpected from e
    version = parse_version(tag_name) if tag_name.startswith("v") else ()
    return Release(tag=tag_name, version=version, assets=assets)


def release(tag: str | None = None) -> Release:
    """The named release, or the newest app release (a vX.Y.Z tag, not a draft or prerelease) when tag is None.

    The repo also holds model releases; GitHub's own "latest" could point at one, so it is not used."""
    if tag:
        return _parse_release(_get_json(f"{API}/tags/{tag}"))
    listing = _get_json(f"{API}?per_page=30")
    if not isinstance(listing, list):
        raise RemoteError("GitHub's release list was not in the expected form")
    for data in listing:
        if (
            isinstance(data, dict)
            and str(data.get("tag_name", "")).startswith("v")
            and not data.get("draft")
            and not data.get("prerelease")
        ):
            return _parse_release(data)
    raise RemoteError("GitHub lists no Vaquero release")


# a failed download is tried again after 2, 4 and 8 s (each plus up to 1 s of jitter), resuming each time
DOWNLOAD_ATTEMPTS = 4
BACKOFF_BASE_S, BACKOFF_CAP_S = 2.0, 30.0


def _backoff(attempt: int) -> float:
    """Capped exponential backoff with jitter, so many clients retrying do not hit the server in step."""
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt) + random.uniform(0, 1)


def _wait(seconds: float, cancelled: Callable[[], bool]) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise DownloadPaused("download paused; it resumes next time")
        time.sleep(0.1)


def download(
    asset: Asset,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Download an asset into DOWNLOADS_DIR, resuming a partial download left by an earlier run.

    Each attempt tries asset.url, then each mirror, resuming the same partial file; a failed attempt waits with
    exponential backoff and tries again, up to DOWNLOAD_ATTEMPTS. Cancelling keeps the partial file. A finished
    file is checked against the asset's digest."""
    last: RemoteError | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        if attempt:
            delay = _backoff(attempt - 1)
            log.warning("%s; trying again in %.0f s (attempt %d of %d)", last, delay, attempt + 1, DOWNLOAD_ATTEMPTS)
            _wait(delay, cancelled)
        for url in (asset.url, *asset.mirrors):
            try:
                return _download_once(asset, url, progress, cancelled)
            except DownloadPaused:
                raise
            except RemoteError as e:
                last = e
                if url != asset.url or asset.mirrors:
                    log.warning("%s from %s failed: %s", asset.name, urllib.parse.urlsplit(url).netloc, e)
    raise last or RemoteError(f"download of {asset.name} failed")


def _download_once(asset: Asset, url: str, progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> Path:
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
        headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
        # the URL redirects to GitHub's storage; the Range header goes along with it
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.URLError as e:
            raise RemoteError(f"download of {asset.name} failed: {e.reason}") from e
        except (OSError, http.client.HTTPException) as e:
            raise RemoteError(f"download of {asset.name} failed: {e}") from e
        with resp:
            if have and resp.status != 206:
                have = 0  # the server ignored the range: start over
            if have:
                log.info("resuming %s at %.0f of %.0f MB", asset.name, have / 1e6, asset.size / 1e6)
            # a write error (disk full) is the Mac's own and is raised as is; only the network is worth retrying
            with part.open("ab" if have else "wb") as fh:
                while True:
                    try:
                        chunk = resp.read(CHUNK)
                    except (OSError, http.client.HTTPException) as e:  # dropped mid-file: the next attempt resumes
                        raise RemoteError(f"download of {asset.name} stopped at {have / 1e6:.0f} MB: {e}") from e
                    if not chunk:
                        break
                    fh.write(chunk)
                    have += len(chunk)
                    progress(have, asset.size)
                    if cancelled():
                        raise DownloadPaused(f"download paused at {have / 1e6:.0f} MB; it resumes next time")
    if part.stat().st_size != asset.size:
        raise RemoteError(f"{asset.name} is incomplete ({part.stat().st_size / 1e6:.0f} of {asset.size / 1e6:.0f} MB)")
    if not _digest_ok(part, asset):
        part.unlink()
        raise RemoteError(f"{asset.name} did not match its checksum and was discarded")
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
    if pack.is_dir() and pack_models(pack) and pack_intact(pack):
        return pack
    if pack.is_dir():
        log.warning("model pack %s is incomplete or damaged; downloading it again", DEFAULT_PACK)
    archive = download(MODEL_ARCHIVE, progress, cancelled)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SUPPORT_DIR) as tmp:
        staging = Path(tmp) / DEFAULT_PACK
        try:
            with zipfile.ZipFile(archive) as z:
                for member in z.namelist():
                    name = Path(member).name
                    if name in MODEL_FILES:
                        staging.mkdir(exist_ok=True)
                        (staging / name).write_bytes(z.read(member))
        except zipfile.BadZipFile as e:
            archive.unlink(missing_ok=True)
            raise RemoteError(f"{MODEL_ARCHIVE.name} is damaged and was discarded; try again") from e
        if not pack_models(staging) or not pack_intact(staging, verify=True):
            raise RemoteError(f"{MODEL_ARCHIVE.name} does not hold the expected {' and '.join(MODEL_FILES)}")
        if pack.exists():
            shutil.rmtree(pack)
        staging.rename(pack)
    archive.unlink()
    log.info("model pack %s installed in %s", DEFAULT_PACK, pack)
    return pack


def pack_intact(pack: Path, verify: bool = False) -> bool:
    """The default pack's model files have their pinned sizes; with verify, their pinned SHA-256 too (slower, used
    once on install). A file cut short by a full disk or a crash would otherwise fail only when a scan loads it."""
    for name, (size, sha) in MODEL_FILES.items():
        f = pack / name
        try:
            if f.stat().st_size != size:
                return False
        except OSError:
            return False
        if verify and not _digest_ok(f, Asset(name, size, "", f"sha256:{sha}")):
            return False
    return True


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


APP_ID = "com.bitwise0x.vaquero"
UPDATE_LOG = LOG_DIR / "vaquero-update.log"
# written by the install script when it could not swap the app, read and removed at the next launch
UPDATE_FAILED = SUPPORT_DIR / "update-failed.txt"
QUIT_WAIT_S = 120  # how long the install script waits for the old app to quit


def install_location_problem(bundle: Path) -> str | None:
    """Why the app cannot replace itself where it is, or None. Checked before an update is downloaded."""
    if "/AppTranslocation/" in str(bundle):
        return (
            "macOS is running Vaquero from a temporary copy, so it cannot update itself. Drag Vaquero into the "
            "Applications folder, open it from there, then update."
        )
    if not os.access(bundle.parent, os.W_OK) or not os.access(bundle, os.W_OK):
        if str(bundle).startswith("/Volumes/"):
            return (
                "Vaquero is running from its disk image or another drive it cannot write to. Drag Vaquero into the "
                "Applications folder, open it from there, then update."
            )
        return (
            f"This Mac account cannot replace Vaquero in {bundle.parent}. Update from an administrator account, "
            "or download the new version from the website."
        )
    return None


def _team_id(app: Path) -> str:
    out = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)], capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.split("=", 1)[1].strip()
            return "" if value == "not set" else value
    return ""


def verify_update(staged: Path, running: Path, version: tuple[int, ...]) -> None:
    """Accept the staged app only if it is Vaquero, signed by the same Developer ID team as the running app, passes
    Gatekeeper (Developer ID and notarization), and is the version the release announced. The same checks Sparkle
    makes before it installs an update."""
    team = _team_id(running)
    if not team:
        raise RemoteError("this copy of Vaquero is not signed, so it cannot check an update; download it from the website")
    requirement = f'anchor apple generic and identifier "{APP_ID}" and certificate leaf[subject.OU] = "{team}"'
    checks = (
        (["codesign", "--verify", "--deep", "--strict", f"-R={requirement}", str(staged)], "its signature is not Vaquero's"),
        (["spctl", "--assess", "--type", "execute", str(staged)], "macOS Gatekeeper rejected it"),
    )
    for cmd, why in checks:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            log.error("update check failed: %s: %s", " ".join(cmd[:2]), (r.stderr or r.stdout).strip())
            raise RemoteError(f"the update was discarded: {why}")
    try:
        with (staged / "Contents" / "Info.plist").open("rb") as fh:
            found = parse_version(str(plistlib.load(fh).get("CFBundleShortVersionString", "")))
    except (OSError, plistlib.InvalidFileException, ValueError) as e:
        raise RemoteError("the update was discarded: its version could not be read") from e
    if found != version:
        shown = ".".join(map(str, found)) or "none"
        raise RemoteError(f"the update was discarded: it holds version {shown}, not {'.'.join(map(str, version))}")


def stage_update(dmg: Path, workdir: Path) -> Path:
    """Copy Vaquero.app out of a downloaded DMG into workdir; returns the staged bundle."""
    mount = workdir / "mnt"
    mount.mkdir()
    try:
        subprocess.run(
            ["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mount)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RemoteError(f"the update image could not be opened: {(e.stderr or '').strip() or e}") from e
    try:
        apps = list(mount.glob("*.app"))
        if len(apps) != 1:
            raise RemoteError(f"expected one app in the update image, found {len(apps)}")
        staged = workdir / apps[0].name
        try:
            subprocess.run(["ditto", str(apps[0]), str(staged)], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise RemoteError(f"the update could not be copied out of its image: {(e.stderr or '').strip()}") from e
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], capture_output=True)
    return staged


def install_and_relaunch(staged: Path, bundle: Path, pid: int, opener: str = "open") -> None:
    """Replace bundle with staged once this process (pid) has exited, then reopen it.

    A detached shell does the swap, since a running app cannot replace its own bundle. staged sits beside bundle,
    so each move is a rename on one volume. The old app is put back if the new one cannot be moved in; either way
    the script logs to vaquero-update.log, and a failure leaves its reason in UPDATE_FAILED for the next launch."""
    q = shlex.quote
    b, old, marker = q(str(bundle)), q(f"{bundle}.old"), q(str(UPDATE_FAILED))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script = staged.parent / "install.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"exec >>{q(str(UPDATE_LOG))} 2>&1\n"
        f'echo "$(date "+%Y-%m-%d %H:%M:%S") installing {q(staged.name)} over {b}"\n'
        "n=0\n"
        f"while kill -0 {pid} 2>/dev/null; do\n"
        f"  n=$((n + 1)); if [ $n -gt {QUIT_WAIT_S * 2} ]; then\n"
        f"    echo 'Vaquero did not quit; update not installed'; echo 'Vaquero did not quit in time' > {marker}; exit 1\n"
        "  fi; sleep 0.5\n"
        "done\n"
        f"rm -rf {old}\n"
        f"if mv {b} {old}; then\n"
        f"  if mv {q(str(staged))} {b}; then rm -rf {old}; echo installed\n"
        f"  else mv {old} {b}; echo 'could not move the new version in; kept the old one'\n"
        f"    echo 'the new version could not be moved into place' > {marker}; fi\n"
        f"else echo 'could not move the old version aside'; echo 'the app could not be replaced in its folder' > {marker}\n"
        "fi\n"
        f"xattr -dr com.apple.quarantine {b} 2>/dev/null\n"
        f"{q(opener)} {b}\n"
        f"rm -rf {q(str(staged.parent))}\n"
    )
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True)


def take_update_failure() -> str | None:
    """The reason the last update could not be installed, once; None when it was or there was none."""
    try:
        reason = UPDATE_FAILED.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    UPDATE_FAILED.unlink(missing_ok=True)
    return reason or "unknown"
