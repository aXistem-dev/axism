"""CLAUDE_CONFIG_DIR reaches every ``claude`` subprocess for a non-default root."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from axism import live as live_mod
from axism.discover import SessionMeta
from axism.live import LiveSession
from axism.providers.claude_code import ClaudeCodeProvider

SID = "11111111-1111-1111-1111-111111111111"


class _Rec:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict | None]] = []

    def run(self, cmd, **kw):
        self.calls.append((list(cmd), kw.get("env")))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _Rec:
    r = _Rec()
    monkeypatch.setattr(subprocess, "run", r.run)
    monkeypatch.setattr(live_mod.shutil, "which", lambda name: "/bin/claude")
    return r


@pytest.fixture
def default_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "default"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


def _bg() -> LiveSession:
    return LiveSession(
        session_id=SID, kind="background", state="working", daemon_short=SID[:8]
    )


def test_non_default_root_sets_env_on_all_subprocesses(
    rec: _Rec, default_root: Path, tmp_path: Path
):
    other = tmp_path / "other"
    other.mkdir()
    p = ClaudeCodeProvider(config_dir=other)
    p.stop(SID, _bg())
    p.slash_stop(SID, _bg())
    p.purge_project("/home/alice/src/demo", dry_run=True)
    p.merge_live(use_cli=True)
    assert len(rec.calls) >= 4
    for cmd, env in rec.calls:
        assert env is not None, cmd
        assert env["CLAUDE_CONFIG_DIR"] == str(other.resolve())
        assert "PATH" in env


def test_default_root_leaves_env_alone(rec: _Rec, default_root: Path):
    p = ClaudeCodeProvider(config_dir=default_root)
    p.slash_stop(SID, _bg())
    p.purge_project("/home/alice/src/demo", dry_run=True)
    assert rec.calls
    assert all(env is None for _cmd, env in rec.calls)


def test_resume_exec_gets_env_for_non_default_root(
    monkeypatch: pytest.MonkeyPatch, default_root: Path, tmp_path: Path
):
    other = tmp_path / "other"
    other.mkdir()
    seen: dict = {}
    monkeypatch.setattr(live_mod.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(
        os, "execvpe", lambda f, a, e: seen.update(file=f, argv=a, env=e)
    )
    monkeypatch.setattr(os, "execvp", lambda f, a: seen.update(plain=True))
    sess = SessionMeta(
        session_id=SID, project_slug="x", cwd=None, title=None,
        first_prompt=None, transcript_path=other / "x.jsonl", mtime=0.0, size_bytes=0,
    )
    ClaudeCodeProvider(config_dir=other).resume(sess, None)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(other.resolve())
    assert seen["argv"][:2] == ["claude", "--resume"]

    seen.clear()
    ClaudeCodeProvider(config_dir=default_root).resume(sess, None)
    assert seen == {"plain": True}
