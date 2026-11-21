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
