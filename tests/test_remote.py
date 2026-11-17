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
