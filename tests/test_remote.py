import email.message
import http.client
import io
import json
import urllib.error
from pathlib import Path

import pytest

from vaquero import remote


def test_legacy_folders_and_journal_move_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs, support = tmp_path / "Logs" / "Vaquero", tmp_path / "Support" / "Vaquero"
    old_logs, old_support = logs.with_name("FaceSort"), support.with_name("FaceSort")
    (old_logs).mkdir(parents=True)
    (old_logs / "facesort-1.log").write_text("log")
    (old_support / "models" / "buffalo_l").mkdir(parents=True)
    (old_support / "facesort-moves.csv").write_text("a,b,moved\n")
    monkeypatch.setattr(remote, "LOG_DIR", logs)
    monkeypatch.setattr(remote, "SUPPORT_DIR", support)
    monkeypatch.setattr(remote, "JOURNAL", support / "vaquero-moves.csv")

    remote.migrate_legacy()
    remote.migrate_legacy()

    assert not old_logs.exists() and not old_support.exists()
    assert (logs / "facesort-1.log").read_text() == "log"
    assert (support / "models" / "buffalo_l").is_dir()
    assert (support / "vaquero-moves.csv").read_text() == "a,b,moved\n"


def test_existing_vaquero_folders_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    support = tmp_path / "Vaquero"
    support.mkdir()
    (support.with_name("FaceSort")).mkdir()
    monkeypatch.setattr(remote, "LOG_DIR", tmp_path / "Logs" / "Vaquero")
    monkeypatch.setattr(remote, "SUPPORT_DIR", support)
    monkeypatch.setattr(remote, "JOURNAL", support / "vaquero-moves.csv")

    remote.migrate_legacy()

    assert support.with_name("FaceSort").is_dir()


class _Answer(io.BytesIO):
    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _serve(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    def urlopen(req: object, timeout: float = 0) -> object:
        if isinstance(answer, BaseException):
            raise answer
        return _Answer(answer if isinstance(answer, bytes) else json.dumps(answer).encode())

    monkeypatch.setattr(remote.urllib.request, "urlopen", urlopen)


@pytest.mark.parametrize(
    "answer",
    [
        b"<html><body>Log in to hotel Wi-Fi</body></html>",
        http.client.IncompleteRead(b"[{", 4000),
        http.client.BadStatusLine("garbage"),
        ConnectionResetError(54, "Connection reset by peer"),
        {"message": "not a list"},
        [{"tag_name": "v1.0.0"}],
        [{"tag_name": "v1.0.0", "assets": [{"name": "x"}]}],
    ],
)
def test_update_check_turns_any_bad_answer_into_a_remote_error(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    """Each of these once escaped the worker, which then never finished and kept the window busy for good."""
    _serve(monkeypatch, answer)
    with pytest.raises(remote.RemoteError):
        remote.release()


def test_latest_release_skips_model_releases_drafts_and_prereleases(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = {"name": "Vaquero.dmg", "size": 3, "browser_download_url": "https://x/Vaquero.dmg", "digest": ""}
    _serve(monkeypatch, [
        {"tag_name": "model-buffalo_l-1", "assets": [], "draft": False, "prerelease": False},
        {"tag_name": "v2.0.0", "assets": [], "draft": True, "prerelease": False},
        {"tag_name": "v1.9.0-beta.1", "assets": [], "draft": False, "prerelease": True},
        {"tag_name": "v1.8.0", "assets": [asset], "draft": False, "prerelease": False},
    ])

    latest = remote.release()

    assert latest.tag == "v1.8.0" and latest.version == (1, 8, 0) and "Vaquero.dmg" in latest.assets


def test_rate_limit_says_when_to_try_again(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = email.message.Message()
    headers["x-ratelimit-reset"] = "1795000000"
    _serve(monkeypatch, urllib.error.HTTPError("https://api.github.com", 403, "rate limit", headers, None))

    with pytest.raises(remote.RemoteError, match="try again after"):
        remote.release()


def test_install_location_problems(tmp_path: Path) -> None:
    app = tmp_path / "Vaquero.app"
    app.mkdir()
    assert remote.install_location_problem(app) is None
    assert "temporary copy" in remote.install_location_problem(
        Path("/private/var/folders/xx/T/AppTranslocation/ABC/d/Vaquero.app")
    )
    tmp_path.chmod(0o555)
    try:
        assert "cannot replace Vaquero" in remote.install_location_problem(app)
    finally:
        tmp_path.chmod(0o755)


def _fake_app(path: Path, version: str) -> Path:
    import plistlib

    (path / "Contents" / "MacOS").mkdir(parents=True)
    with (path / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "com.bitwise0x.vaquero", "CFBundleShortVersionString": version,
                       "CFBundleExecutable": "Vaquero"}, fh)
    exe = path / "Contents" / "MacOS" / "Vaquero"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return path


def test_an_unsigned_or_ad_hoc_update_is_refused(tmp_path: Path) -> None:
    import subprocess

    staged = _fake_app(tmp_path / "staged" / "Vaquero.app", "9.9.9")
    subprocess.run(["codesign", "-s", "-", "--force", str(staged)], check=True, capture_output=True)
    signed = Path(__file__).resolve().parent.parent / "dist" / "Vaquero.app"
    if not signed.exists():
        pytest.skip("needs a Developer ID signed build in dist/")

    with pytest.raises(remote.RemoteError, match="signature is not Vaquero's"):
        remote.verify_update(staged, signed, (9, 9, 9))
    with pytest.raises(remote.RemoteError, match="not signed"):
        remote.verify_update(signed, staged, (9, 9, 9))


def test_a_signed_update_of_the_wrong_version_is_refused() -> None:
    signed = Path(__file__).resolve().parent.parent / "dist" / "Vaquero.app"
    if not signed.exists():
        pytest.skip("needs a Developer ID signed build in dist/")
    import plistlib

    with (signed / "Contents" / "Info.plist").open("rb") as fh:
        version = remote.parse_version(plistlib.load(fh)["CFBundleShortVersionString"])

    remote.verify_update(signed, signed, version)
    with pytest.raises(remote.RemoteError, match="holds version"):
        remote.verify_update(signed, signed, (*version[:-1], version[-1] + 1))


def _run_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, writable: bool = True) -> tuple[Path, Path]:
    import subprocess

    monkeypatch.setattr(remote, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(remote, "UPDATE_LOG", tmp_path / "logs" / "vaquero-update.log")
    monkeypatch.setattr(remote, "UPDATE_FAILED", tmp_path / "update-failed.txt")
    apps = tmp_path / "Apps with 'quotes' & $pace"
    bundle = _fake_app(apps / "Vaquero.app", "1.0.0")
    work = apps / ".vaquero-update-x"
    staged = _fake_app(work / "Vaquero.app", "2.0.0")
    quitter = subprocess.Popen(["sleep", "0.3"])
    if not writable:
        (bundle / "Contents" / "Info.plist").chmod(0o444)
        apps.chmod(0o555)
    opened = tmp_path / "opened"
    opener = tmp_path / "opener.sh"
    opener.write_text(f'#!/bin/sh\necho "$1" > "{opened}"\n')
    opener.chmod(0o755)
    remote.install_and_relaunch(staged, bundle, quitter.pid, opener=str(opener))
    quitter.wait()
    for _ in range(100):
        if opened.exists():
            break
        import time

        time.sleep(0.05)
    if not writable:
        apps.chmod(0o755)
    return bundle, opened


def test_the_installer_swaps_the_app_after_it_quits_and_reopens_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plistlib
    import time

    bundle, opened = _run_installer(tmp_path, monkeypatch)
    time.sleep(0.2)

    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        assert plistlib.load(fh)["CFBundleShortVersionString"] == "2.0.0"
    assert opened.read_text().strip() == str(bundle)
    assert not bundle.with_name("Vaquero.app.old").exists()
    assert not (bundle.parent / ".vaquero-update-x").exists()
    assert "installed" in remote.UPDATE_LOG.read_text()
    assert remote.take_update_failure() is None


def test_a_failed_swap_keeps_the_old_app_and_says_why_next_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plistlib

    bundle, opened = _run_installer(tmp_path, monkeypatch, writable=False)

    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        assert plistlib.load(fh)["CFBundleShortVersionString"] == "1.0.0"
    assert opened.read_text().strip() == str(bundle)
    assert remote.take_update_failure() == "the app could not be replaced in its folder"
    assert remote.take_update_failure() is None
