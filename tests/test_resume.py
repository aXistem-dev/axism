from pathlib import Path

import pytest

from axism.live import LiveSession, open_command, resume_command


def test_resume_command_requires_claude(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("axism.live.shutil.which", lambda _cmd: None)
    with pytest.raises(FileNotFoundError):
        resume_command("11111111-1111-1111-1111-111111111111")


def test_resume_command_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("axism.live.shutil.which", lambda _cmd: "/usr/bin/claude")
    argv, cwd = resume_command(
        "11111111-1111-1111-1111-111111111111", cwd=str(tmp_path)
    )
    assert argv == ["claude", "--resume", "11111111-1111-1111-1111-111111111111"]
    assert cwd == str(tmp_path)


def test_open_command_attach_for_blocked_bg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("axism.live.shutil.which", lambda _cmd: "/usr/bin/claude")
    sid = "7347073a-2da9-45a4-90b7-ff70296c0931"
    live = LiveSession(
        session_id=sid, kind="background", state="blocked", daemon_short="7347073a"
    )
    argv, _ = open_command(sid, live=live)
    assert argv == ["claude", "attach", "7347073a"]


def test_open_command_resume_when_not_live_bg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("axism.live.shutil.which", lambda _cmd: "/usr/bin/claude")
    sid = "11111111-1111-1111-1111-111111111111"
    live = LiveSession(session_id=sid, kind="background", state="done")
    argv, _ = open_command(sid, live=live)
    assert argv == ["claude", "--resume", sid]
