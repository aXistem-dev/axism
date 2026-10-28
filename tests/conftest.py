"""Synthetic Claude Code config fixtures (alice paths only)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

DEMO_CWD = "/home/alice/src/demo"
DEMO_SLUG = "-home-alice-src-demo"
SESSION_A = "11111111-1111-1111-1111-111111111111"
SESSION_B = "22222222-2222-2222-2222-222222222222"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def build_fake_config(root: Path) -> Path:
    """Create a minimal ~/.claude-like tree under root."""
    proj = root / "projects" / DEMO_SLUG
    proj.mkdir(parents=True)

    write_jsonl(
        proj / f"{SESSION_A}.jsonl",
        [
            {"type": "mode", "mode": "normal", "sessionId": SESSION_A},
            {
                "type": "ai-title",
                "title": "Demo session A",
                "sessionId": SESSION_A,
            },
            {
                "type": "user",
                "sessionId": SESSION_A,
                "cwd": DEMO_CWD,
                "isSidechain": False,
                "message": {"role": "user", "content": "Hello from alice demo"},
                "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "timestamp": "2026-01-01T00:00:00.000Z",
            },
        ],
    )
    # session subdir with subagent + tool result
    sub = proj / SESSION_A / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-deadbeef.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": SESSION_A, "isSidechain": True})
        + "\n",
        encoding="utf-8",
    )
    (proj / SESSION_A / "tool-results").mkdir()
    (proj / SESSION_A / "tool-results" / "out.txt").write_text("tool out", encoding="utf-8")

    write_jsonl(
        proj / f"{SESSION_B}.jsonl",
        [
            {
                "type": "custom-title",
                "customTitle": "Background demo",
                "sessionId": SESSION_B,
            },
            {
                "type": "user",
                "sessionId": SESSION_B,
                "cwd": DEMO_CWD,
                "isSidechain": False,
                "message": {"role": "user", "content": "bg work"},
                "uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "timestamp": "2026-01-02T00:00:00.000Z",
            },
        ],
    )

    # project memory must never be deleted with a session
    mem = proj / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# memory\n", encoding="utf-8")

    # fragments
    (root / "file-history" / SESSION_A).mkdir(parents=True)
    (root / "file-history" / SESSION_A / "backup@v1").write_text("x", encoding="utf-8")
    (root / "uploads" / SESSION_A).mkdir(parents=True)
    (root / "uploads" / SESSION_A / "file.txt").write_text("up", encoding="utf-8")
    (root / "session-env" / SESSION_A).mkdir(parents=True)
    (root / "tasks" / SESSION_A).mkdir(parents=True)
    (root / "tasks" / SESSION_A / ".lock").write_text("", encoding="utf-8")

    job = root / "jobs" / SESSION_B[:8]
    job.mkdir(parents=True)
    (job / "state.json").write_text(
        json.dumps(
            {
                "state": "done",
                "sessionId": SESSION_B,
                "name": "Background demo",
                "cwd": DEMO_CWD,
                "daemonShort": SESSION_B[:8],
            }
        ),
        encoding="utf-8",
    )

    (root / "history.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "display": "Hello from alice demo",
                        "timestamp": 1,
                        "project": DEMO_CWD,
                        "sessionId": SESSION_A,
                    }
                ),
                json.dumps(
                    {
                        "display": "keep me",
                        "timestamp": 2,
                        "project": DEMO_CWD,
                        "sessionId": "33333333-3333-3333-3333-333333333333",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # protected files
    (root / ".credentials.json").write_text('{"token":"nope"}', encoding="utf-8")
    (root / "settings.json").write_text("{}", encoding="utf-8")
    (root / "skills").mkdir()
    (root / "plugins").mkdir()

    return root


import pytest


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = build_fake_config(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def _pinned_tmp_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never resolve scratch roots to the machine's real temp dirs."""
    scratch = tmp_path / "claude-scratch-root"
    for target in (
        "axism.paths.tmp_claude_roots",
        "axism.fragments.tmp_claude_roots",
        "axism.move.tmp_claude_roots",
    ):
        monkeypatch.setattr(target, lambda: [scratch])
    return scratch


_REAL_WHICH = shutil.which
_AGENT_BINARIES = {"claude", "hermes", "cursor-agent", "cursor", "opencode"}


@pytest.fixture(autouse=True)
def _no_real_agent_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may find, and so run, a real agent binary on PATH."""

    def which(cmd, *args, **kwargs):
        if str(cmd) in _AGENT_BINARIES:
            return None
        return _REAL_WHICH(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)


@pytest.fixture(autouse=True)
def _isolated_axism_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detection writes settings; never let a test reach the real config dir."""
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axism-config-default"))


@pytest.fixture(autouse=True)
def _isolated_opencode_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the machine's real OpenCode data or state folder."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data-default"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state-default"))


@pytest.fixture(autouse=True)
def _isolated_agent_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may stat the machine's real agent folders or home directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home-default"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-default"))
    monkeypatch.setenv("CURSOR_DATA_DIR", str(tmp_path / "cursor-default"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-default"))
