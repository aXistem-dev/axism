"""Process matching and signalling helpers shared by Cursor and Hermes."""

from __future__ import annotations

import os
import signal

import pytest

from axism.providers import common
from axism.providers.common import find_agent_sessions, terminate_pid

SID = "20260101_090000_aaaaaa"
MARKERS = ("hermes_cli/main.py", "hermes_cli.main")

LAUNCHER = (
    "/home/alice/.hermes/venv/bin/python3 -I -c "
    '"import sys, runpy; sys.path.insert(0, \'/home/alice/hermes-agent\'); '
    "sys.argv = ['/home/alice/hermes-agent/hermes_cli/main.py', '--resume', "
    f"'{SID}']; runpy.run_path(sys.argv[0], run_name='__main__')\""
)


def _table(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[int, str]]) -> None:
    monkeypatch.setattr(common, "process_table", lambda: rows)


def test_launcher_shaped_hermes_process_is_found(monkeypatch: pytest.MonkeyPatch):
    _table(monkeypatch, [(4242, LAUNCHER)])
    assert find_agent_sessions({SID}, {"hermes"}, markers=MARKERS) == {SID: 4242}
    assert find_agent_sessions({SID}, {"hermes"}) == {}


def test_plain_binary_is_still_found(monkeypatch: pytest.MonkeyPatch):
    _table(monkeypatch, [(7, f"/usr/bin/hermes --resume {SID}")])
    assert find_agent_sessions({SID}, {"hermes"}, markers=MARKERS) == {SID: 7}


@pytest.mark.parametrize("mux", ["tmux", "/usr/bin/screen", "zellij", "tmux:"])
def test_multiplexer_wrapping_the_agent_is_skipped(
    monkeypatch: pytest.MonkeyPatch, mux: str
):
    _table(monkeypatch, [(9, f"{mux} new -d hermes --resume {SID}")])
    assert find_agent_sessions({SID}, {"hermes"}, markers=MARKERS) == {}


def test_terminate_refuses_when_the_pid_changed_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(common, "read_cmdline", lambda pid: "/bin/sleep 100")
    ok, msg = terminate_pid(31337, expect=SID)
    assert ok is False
    assert msg == f"pid 31337 no longer runs {SID}"
    assert signal.SIGTERM not in [sig for _, sig in sent]


def test_terminate_signals_when_identity_matches(monkeypatch: pytest.MonkeyPatch):
    alive = {"v": True}
    sent: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if not alive["v"]:
                raise ProcessLookupError
            return
        sent.append(sig)
        alive["v"] = False

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(common, "read_cmdline", lambda pid: f"hermes --resume {SID}")
    ok, _msg = terminate_pid(31337, expect=SID)
    assert ok is True
    assert sent == [signal.SIGTERM]
