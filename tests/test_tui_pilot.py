"""Textual pilot tests for the TUI against synthetic Claude Code fixtures."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from conftest import DEMO_SLUG, SESSION_A, SESSION_B, build_fake_config, write_jsonl
from textual.widgets import DataTable, Input

import axism.tui.app as tui
from axism.actions import actions_failed
from axism.tui.app import SessionManagerApp
from axism.tui.dialogs import ConfirmDeleteScreen


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fake: bool = True) -> Path:
    claude = tmp_path / "claude"
    claude.mkdir()
    if fake:
        build_fake_config(claude)
    cfg = tmp_path / "axcfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "active_provider": "claude_code",
                "providers": {
                    "claude_code": {"enabled": True, "config_dir": str(claude)},
                    "cursor": {"enabled": False, "config_dir": str(tmp_path / "cur")},
                    "hermes": {"enabled": False, "config_dir": str(tmp_path / "her")},
                },
            }
        )
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True: real(provs, use_cli=False)
    )
    return claude


def _run(coro):
    return asyncio.run(coro)


# ---- E2 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "aborted: could not stop live session (11111111)",
        "aborted: some reason",
        "aborted 11111111: some reason",
        "aborted: project folder kept because a live session could not be stopped; 1 other session(s) deleted",
        "aborted: a live session could not be stopped; 0 other session(s) deleted",
        "error: hermes sessions delete abc failed: no such session",
        "refused: /x/y outside /x/projects",
        "refused: /x/y outside the session stores",
        "kill(123) failed: boom",
        "SIGKILL 123 failed: boom",
        "pid 123 still alive after KILL",
        "stop/kill: stop/kill/rm failed",
        "11111111: stop: pid 5 no longer runs claude",
        "stop/kill: pid 5 no longer runs claude",
        "error removing /x: nope",
    ],
)
def test_actions_failed_detects_failure_lines(line):
    assert actions_failed(["removed a", line])


@pytest.mark.parametrize(
    "line",
    [
        "removed /x/y",
        "would remove project dir /x (10 B)",
        "stop/kill: stopped pid 5 (TERM)",
        "11111111: stop: pid 5 already exited",
        "nothing to delete",
    ],
)
def test_actions_failed_ignores_success_lines(line):
    assert not actions_failed([line])


def test_summaries_name_the_failure_line():
    ok, msg = tui._summarize_session_delete(
        [], ["stop/kill: kill(5) failed: x", "aborted: could not stop live session"]
    )
    assert not ok and "Delete failed" in msg
    ok, msg = tui._summarize_project_delete(["s"], ["refused: /a outside /b"])
    assert not ok and "refused" in msg


# ---- E1 -------------------------------------------------------------------


def test_project_delete_default_removes_memory(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)
    proj = claude / "projects" / DEMO_SLUG

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#projects").focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            text = str(app.screen.query_one("#confirm-memory").render())
            assert "memory/" in text and "DELETED" in text
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert not proj.exists()


def test_project_delete_keep_memory_toggle(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)
    proj = claude / "projects" / DEMO_SLUG

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#projects").focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            line = app.screen.query_one("#confirm-memory")
            body = app.screen.query_one("#confirm-body")
            before = (str(line.render()), str(body.render()))
            assert "DELETED" in before[0]
            await pilot.press("m")
            await pilot.pause()
            after = (str(line.render()), str(body.render()))
            assert "KEPT" in after[0]
            assert "keeping memory/" in after[1]
            assert before[1] != after[1]
            await pilot.press("m")
            await pilot.pause()
            assert "DELETED" in str(line.render())
            await pilot.press("m")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert (proj / "memory" / "MEMORY.md").is_file()
    assert not (proj / f"{SESSION_A}.jsonl").exists()


# ---- E3 -------------------------------------------------------------------


def test_select_all_marks_only_visible_sessions(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)
    proj = claude / "projects" / DEMO_SLUG

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#sessions").focus()
            await pilot.pause()
            app._session_filter = "Demo session A"
            app._reload_sessions_table()
            await pilot.pause()
            assert [s.session_id for s in app._visible_sessions()] == [SESSION_A]
            await pilot.press("a")
            await pilot.pause()
            assert {s.session_id for s in app._target_sessions_for_delete()} == {SESSION_A}
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert not (proj / f"{SESSION_A}.jsonl").exists()
    assert (proj / f"{SESSION_B}.jsonl").exists()


# ---- E4 -------------------------------------------------------------------


def test_empty_project_list_clears_selection(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#projects").focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()
            assert app.projects == []
            assert app.selected_session is None
            assert app.selected_project is None
            detail = str(app.query_one("#detail-body").render())
            assert "Demo session" not in detail
            app.query_one("#sessions").focus()
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            assert app.return_value is None
            assert len(app.screen_stack) == 1
            await pilot.press("n")
            await pilot.pause()
            assert len(app.screen_stack) == 1

    _run(main())


# ---- E5 / E7 --------------------------------------------------------------


def test_narrow_terminal_shows_projects_and_sessions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(60, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            proj = app.query_one("#projects").region
            sess = app.query_one("#sessions-pane").region
            assert proj.width > 0 and sess.width > 0
            assert sess.x < 60
            assert sess.right <= 60
            assert not app.query_one("#detail").display
            for tid in ("#projects", "#sessions"):
                table = app.query_one(tid, DataTable)
                assert table.virtual_size.width <= table.size.width

    _run(main())


def test_wide_terminal_keeps_detail_pane(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#detail").display

    _run(main())


def test_initial_layout_uses_real_width(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(200, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app._projects_layout is not None
            assert app._projects_layout.density == "full"
            assert app._projects_layout == app._compute_projects_layout()
            assert app._sessions_layout == app._compute_sessions_layout()

    _run(main())


# ---- E8 -------------------------------------------------------------------


def test_left_right_in_input_moves_cursor_not_pane(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#sessions").focus()
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            flt = app.query_one("#sessions-filter", Input)
            assert app.focused is flt
            await pilot.press("a", "b", "c")
            await pilot.pause()
            assert flt.cursor_position == 3
            await pilot.press("left")
            await pilot.pause()
            assert app.focused is flt
            assert flt.cursor_position == 2
            await pilot.press("right")
            await pilot.pause()
            assert app.focused is flt
            assert flt.cursor_position == 3

    _run(main())


def test_left_right_still_switch_panes_without_input(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#sessions").focus()
            await pilot.pause()
            await pilot.press("left")
            await pilot.pause()
            assert app._focus_pane() == "projects"

    _run(main())


@pytest.mark.parametrize("size", [(80, 24), (100, 30)])
def test_scrolling_lists_fit_without_clipping(tmp_path, monkeypatch, size):
    claude = _setup(tmp_path, monkeypatch, fake=False)
    for pi in range(40):
        cwd = f"/home/alice/src/proj{pi:02d}"
        slug = cwd.replace("/", "-")
        for i in range(60 if pi == 39 else 1):
            sid = str(uuid.UUID(int=pi * 1000 + i + 1))
            write_jsonl(
                claude / "projects" / slug / f"{sid}.jsonl",
                [
                    {"type": "custom-title", "customTitle": f"t{pi}-{i}", "sessionId": sid},
                    {
                        "type": "user",
                        "sessionId": sid,
                        "cwd": cwd,
                        "isSidechain": False,
                        "message": {"role": "user", "content": "hello"},
                        "uuid": str(uuid.uuid4()),
                        "timestamp": "2026-01-01T00:00:00.000Z",
                    },
                ],
            )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.pause()
            for tid in ("#projects", "#sessions"):
                table = app.query_one(tid, DataTable)
                assert table.row_count > table.size.height
                assert table.show_vertical_scrollbar
                region = table.scrollable_content_region
                assert table.virtual_size.width <= region.width, (tid, size)

    _run(main())


def test_keep_memory_toggle_does_not_rebuild_plans(tmp_path, monkeypatch):
    import axism.delete as delete_mod

    _setup(tmp_path, monkeypatch)
    calls: list[int] = []
    real = delete_mod.merge_live

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(delete_mod, "merge_live", counting)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#projects").focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            opened = len(calls)
            assert opened > 0
            await pilot.press("m")
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert len(calls) == opened
            await pilot.press("n")
            await pilot.pause()

    _run(main())


# ---- OpenCode stop ----------------------------------------------------------

OC_SESSION = "ses_a1AliceTuiStop01"


def _setup_opencode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from axism.live import LiveSession
    from axism.providers.opencode import OpenCodeProvider
    from tests.opencode_fixtures import make_opencode_db

    root = tmp_path / "oc" / "opencode"
    make_opencode_db(
        root,
        sessions=[
            {"id": OC_SESSION, "project_id": "p1", "parent_id": None,
             "directory": "/home/alice/src/demo", "title": "demo",
             "time_created": 1, "time_updated": 2},
        ],
        projects=[{"id": "p1", "worktree": "/home/alice/src/demo"}],
        messages=[{"session_id": OC_SESSION, "type": "user", "seq": 1,
                   "data": {"text": "hi"}}],
    )
    cfg = tmp_path / "axcfg-oc"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "active_provider": "opencode",
                "providers": {
                    "claude_code": {"enabled": False, "config_dir": None},
                    "cursor": {"enabled": False, "config_dir": None},
                    "hermes": {"enabled": False, "config_dir": None},
                    "opencode": {"enabled": True, "config_dir": str(root)},
                },
            }
        )
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    live = LiveSession(
        session_id=OC_SESSION, kind="interactive", state="running",
        pid=None, source="opencode-service",
    )
    monkeypatch.setattr(
        OpenCodeProvider, "merge_live", lambda self, root=None, use_cli=True: {OC_SESSION: live}
    )


async def _stop_via_keys(app: SessionManagerApp) -> str:
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.query_one("#sessions").focus()
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteScreen)
        await pilot.press("y")
        await pilot.pause()
        await pilot.pause()
        assert app.is_running and app.return_code is None
        return str(app.query_one("#toast").render())


def test_tui_stop_on_service_running_opencode_session(tmp_path, monkeypatch):
    from axism.providers.opencode import OpenCodeProvider

    _setup_opencode(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(
        OpenCodeProvider,
        "stop",
        lambda self, sid, live=None: calls.append(sid) or (True, "interrupted"),
    )
    toast = _run(_stop_via_keys(SessionManagerApp()))
    assert calls == [OC_SESSION]
    assert "Stopped" in toast


def test_tui_stop_unsupported_shows_failure_toast(tmp_path, monkeypatch):
    from axism.providers.base import UnsupportedOperation
    from axism.providers.opencode import OpenCodeProvider

    _setup_opencode(tmp_path, monkeypatch)

    def unsupported(self, sid, live=None):
        raise UnsupportedOperation("OpenCode does not support this stop")

    monkeypatch.setattr(OpenCodeProvider, "slash_stop", unsupported)
    toast = _run(_stop_via_keys(SessionManagerApp()))
    assert "Stop failed" in toast and "does not support" in toast


def test_tui_delete_dialog_warns_service_running_opencode_session(tmp_path, monkeypatch):
    _setup_opencode(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#sessions").focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            warnings = list(app.screen.warnings)
            await pilot.press("escape")
            await pilot.pause()
            return warnings

    warnings = _run(main())
    assert any(OC_SESSION[:8] in w and "will stop" in w for w in warnings)


def test_tui_move_refuses_service_running_opencode_session(tmp_path, monkeypatch):
    _setup_opencode(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.query_one("#sessions").focus()
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            return type(app.screen).__name__, str(app.query_one("#toast").render())

    screen, toast = _run(main())
    assert screen != "MoveTargetScreen"
    assert "stop them first" in toast


# ---- a running Claude Code background session --------------------------------


def test_running_background_session_shows_state_blocks_move_and_stops(tmp_path, monkeypatch):
    from axism.live import LiveSession
    from axism.providers.claude_code import ClaudeCodeProvider
    from axism.tui.dialogs import MoveTargetScreen

    _setup(tmp_path, monkeypatch)
    live = {
        SESSION_B: LiveSession(
            session_id=SESSION_B, pid=None, kind="background", state="working"
        )
    }
    monkeypatch.setattr(tui, "federate_merge_live", lambda provs, use_cli=True: live)
    stopped: list[str] = []

    def fake_stop(self, sid, live=None):
        stopped.append(sid)
        return True, "stopped"

    monkeypatch.setattr(ClaudeCodeProvider, "stop", fake_stop)
    monkeypatch.setattr(ClaudeCodeProvider, "slash_stop", fake_stop)

    async def highlight(app, pilot, sid):
        table = app.query_one("#sessions", DataTable)
        table.focus()
        keys = [s.key for s in app.selected_project.sessions]
        row = next(i for i, k in enumerate(keys) if k.endswith(sid))
        table.move_cursor(row=row, animate=False)
        await pilot.pause(0.3)
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await highlight(app, pilot, SESSION_B)
            assert "running in the background" in str(app.query_one("#detail-body").render())
            # Move is refused while the session runs.
            await pilot.press("m")
            await pilot.pause()
            assert not isinstance(app.screen, MoveTargetScreen)
            assert "stop them first" in str(app.query_one("#toast").render())
            # Stop asks first, then stops through the provider.
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()
            assert stopped == [SESSION_B]
            # The other session is not running: nothing to stop.
            await highlight(app, pilot, SESSION_A)
            assert "not running" in str(app.query_one("#detail-body").render())
            await pilot.press("s")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmDeleteScreen)
            assert "Nothing to stop" in str(app.query_one("#toast").render())
            assert stopped == [SESSION_B]

    _run(main())
