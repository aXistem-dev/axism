"""OpenCode provider: registry, live state, stop, open, rename/move/delete."""

from __future__ import annotations

from pathlib import Path

import pytest

from axism import actions as actions_mod
from axism import cli
from axism.live import LiveSession
from axism.providers import PROVIDER_LABELS, get_provider
from axism.providers.base import UnsupportedOperation
from axism.providers.federation import federate_discover
from axism.providers.opencode import OpenCodeProvider
from axism.providers.opencode_store import ops
from axism.providers.opencode_store.discover import SessionRow
from axism.providers.opencode_store.paths import short_id
from axism.settings import ProviderPrefs, Settings, save_settings
from tests.opencode_fixtures import make_opencode_db

A = "ses_a1AliceDemo0001"
B = "ses_b1AliceDemo0002"
C = "ses_c1AliceChild003"
CWD = "/home/alice/one"
DEST = "/home/alice/elsewhere"
SERVICE_PID = 4242


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "data" / "opencode"
    make_opencode_db(
        r,
        sessions=[
            {"id": A, "project_id": "p1", "parent_id": None, "directory": CWD,
             "title": "First", "time_created": 1_000_000, "time_updated": 5_000_000},
            {"id": B, "project_id": "p1", "parent_id": None, "directory": CWD,
             "title": "Second", "time_created": 1_000_000, "time_updated": 6_000_000},
            {"id": C, "project_id": "p1", "parent_id": A, "directory": CWD,
             "title": "child", "time_created": 1_000_000, "time_updated": 2_000_000},
        ],
        projects=[{"id": "p1", "worktree": CWD}],
        messages=[
            {"session_id": A, "type": "user", "seq": 1, "data": {"text": "hello"}},
            {"session_id": B, "type": "user", "seq": 1, "data": {"text": "other"}},
        ],
    )
    return r


@pytest.fixture
def prov(root: Path) -> OpenCodeProvider:
    return OpenCodeProvider(config_dir=root)


def _no_processes(monkeypatch, found=None):
    monkeypatch.setattr(
        "axism.providers.opencode.find_agent_sessions",
        lambda ids, bins, markers=(): {k: v for k, v in (found or {}).items() if k in ids},
    )


def test_registered_and_label_order():
    assert list(PROVIDER_LABELS.values()) == [
        "Claude Code", "Cursor", "Hermes", "OpenCode",
    ]
    p = get_provider("opencode")
    assert isinstance(p, OpenCodeProvider)
    assert p.name == "opencode" and p.label == "OpenCode"
    assert p.config_hint == "$XDG_DATA_HOME/opencode or ~/.local/share/opencode"


def test_capabilities():
    c = OpenCodeProvider.capabilities
    assert not c.slash_stop and not c.fragments
    assert not c.memory_merge and not c.purge_project
    assert c.resume and c.stop and c.rename
    assert c.move_sessions and c.move_projects
    assert c.delete_sessions and c.delete_projects


def test_config_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "x"))
    assert OpenCodeProvider().config_root() == tmp_path / "x" / "opencode"
    assert OpenCodeProvider(config_dir=tmp_path).config_root() == tmp_path.resolve()


def test_discover_through_federation_tags_provider(prov):
    projects = federate_discover([prov])
    assert [p.provider for p in projects] == ["opencode"]
    sessions = projects[0].sessions
    assert {s.session_id for s in sessions} == {A, B}
    assert all(s.provider == "opencode" for s in sessions)


def test_merge_live_union_and_use_cli_false(prov, monkeypatch):
    monkeypatch.setattr(ops, "active_session_ids", lambda r: {A})
    monkeypatch.setattr(ops, "service_info", lambda r: None)
    _no_processes(monkeypatch, {A: 77, B: 88})
    live = prov.merge_live()
    assert live[A].state == "running" and live[A].pid == 77
    assert live[A].source == "opencode-service"
    assert live[B].state == "open" and live[B].pid == 88
    assert C not in live

    only_service = prov.merge_live()
    assert set(only_service) == {A, B}

    def boom(root):
        raise AssertionError("service must not be asked")

    monkeypatch.setattr(ops, "active_session_ids", boom)
    quiet = prov.merge_live(use_cli=False)
    assert quiet[A].state == "open" and quiet[A].pid == 77


def test_merge_live_running_without_client(prov, monkeypatch):
    monkeypatch.setattr(ops, "active_session_ids", lambda r: {A})
    monkeypatch.setattr(ops, "service_info", lambda r: None)
    _no_processes(monkeypatch)
    live = prov.merge_live()
    assert live[A].pid is None and live[A].state == "running"
    assert prov.can_stop(live[A])


def test_can_stop(prov):
    assert not prov.can_stop(None)
    assert prov.can_stop(LiveSession(A, "interactive", state="running"))
    assert prov.can_stop(LiveSession(A, "interactive", state="open", pid=5))
    assert not prov.can_stop(LiveSession(A, "interactive", state="open"))


def test_stop_interrupt_then_client_and_failure_reporting(prov, monkeypatch):
    calls: list = []
    monkeypatch.setattr(ops, "service_info", lambda r: None)

    def interrupt(root, row):
        calls.append(("interrupt", row.id))
        return True, "interrupted"

    def term(pid, expect=None):
        calls.append(("term", pid, expect))
        return True, f"stopped pid {pid}"

    monkeypatch.setattr(ops, "interrupt_session", interrupt)
    monkeypatch.setattr("axism.providers.opencode.terminate_pid", term)
    live = LiveSession(A, "interactive", state="running", pid=77)
    ok, msg = prov.stop(A, live)
    assert ok and calls == [("interrupt", A), ("term", 77, A)]
    assert "interrupted" in msg and "stopped pid 77" in msg

    calls.clear()
    monkeypatch.setattr(
        ops, "interrupt_session", lambda r, row: (False, "error: not running")
    )
    ok, msg = prov.stop(A, live)
    assert not ok and "error: not running" in msg and "stopped pid 77" in msg

    calls.clear()
    ok, msg = prov.stop(A, LiveSession(A, "interactive", state="open", pid=77))
    assert ok and calls == [("term", 77, A)]

    monkeypatch.setattr(
        "axism.providers.opencode.terminate_pid",
        lambda pid, expect=None: (False, "pid still alive after KILL"),
    )
    ok, msg = prov.stop(A, LiveSession(A, "interactive", state="open", pid=77))
    assert not ok and "still alive" in msg


def test_stop_never_targets_service_pid(prov, monkeypatch):
    from axism.providers.opencode_store.ops import ServiceInfo

    monkeypatch.setattr(
        ops, "service_info", lambda r: ServiceInfo(SERVICE_PID, "", "2.1.4")
    )

    def term(pid, expect=None):
        raise AssertionError("must not signal")

    monkeypatch.setattr("axism.providers.opencode.terminate_pid", term)
    ok, msg = prov.stop(A, LiveSession(A, "interactive", state="open", pid=SERVICE_PID))
    assert not ok and msg.startswith("refused:")

    monkeypatch.setattr(ops, "active_session_ids", lambda r: set())
    _no_processes(monkeypatch, {A: SERVICE_PID})
    assert A not in prov.merge_live()


def test_slash_stop_unsupported(prov):
    with pytest.raises(UnsupportedOperation):
        prov.slash_stop(A)


def test_open_argv_and_custom_root_env(prov, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda c, *a, **k: "/bin/opencode")
    sess = prov.find_session(A)[1]
    argv, workdir = OpenCodeProvider().open_command(sess)
    assert argv[0] == "opencode" and "--standalone" not in argv
    assert A in argv and workdir == CWD

    seen: dict = {}
    monkeypatch.setattr(
        "axism.providers.opencode.exec_with_cwd",
        lambda a, w: seen.update(argv=a, workdir=w),
    )
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    # prov root is tmp/data/opencode: a custom folder named opencode
    prov.resume(sess)
    assert "--standalone" in seen["argv"]
    import os
    assert os.environ["XDG_DATA_HOME"] == str(prov.config_root().parent)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    odd = OpenCodeProvider(config_dir=tmp_path / "elsewhere")
    with pytest.raises(UnsupportedOperation):
        odd.open_command(sess)


def test_open_command_needs_binary(prov):
    sess = prov.find_session(A)[1]
    with pytest.raises(FileNotFoundError):
        prov.open_command(sess)


def test_rename_calls_ops_with_row(prov, monkeypatch):
    seen: dict = {}

    def rename(root, row, title):
        seen.update(root=root, row=row, title=title)
        return True, "renamed"

    monkeypatch.setattr(ops, "rename_session", rename)
    assert prov.rename(A, "New") == (True, "renamed")
    assert isinstance(seen["row"], SessionRow) and seen["row"].id == A
    assert seen["title"] == "New" and seen["root"] == prov.config_root()
    ok, msg = prov.rename("ses_zzzz", "x")
    assert not ok and actions_mod.is_failure_line(msg)
    ok, msg = prov.rename("not-an-id", "x")
    assert not ok and actions_mod.is_failure_line(msg)


def test_move_sessions_stops_live_first_and_aborts_on_failed_stop(prov, monkeypatch):
    order: list = []
    sessions = [s for p in prov.discover() for s in p.sessions]
    live = {
        A: LiveSession(A, "interactive", state="open", pid=77),
        B: LiveSession(B, "interactive", state="open", pid=88),
    }
    monkeypatch.setattr(prov, "merge_live", lambda *a, **k: live)

    def stop(sid, lv=None):
        order.append(("stop", sid))
        return (sid == A), ("stopped" if sid == A else "pid still alive after KILL")

    monkeypatch.setattr(prov, "stop", stop)

    def move(root, row, directory, **kw):
        order.append(("move", row.id, directory))
        return True, f"moved {short_id(row.id)} → {directory}"

    monkeypatch.setattr(ops, "move_session", move)
    res = prov.move_sessions(sessions, DEST)
    assert ("move", B, DEST) not in order
    assert ("move", A, DEST) in order
    assert order.index(("stop", A)) < order.index(("move", A, DEST))
    assert res.ok and res.dest_cwd == DEST
    assert any(
        a == f"aborted: could not stop live session ({short_id(B)})"
        for a in res.actions
    )


def test_move_sessions_skips_same_directory_and_passes_service_message(prov, monkeypatch):
    sessions = [s for p in prov.discover() for s in p.sessions]
    monkeypatch.setattr(prov, "merge_live", lambda *a, **k: {})

    def move(*a, **k):
        raise AssertionError("must not move")

    monkeypatch.setattr(ops, "move_session", move)
    res = prov.move_sessions(sessions, CWD)
    assert res.ok
    assert all(a.startswith("unchanged: ") for a in res.actions)
    assert not actions_mod.actions_failed(res.actions)

    monkeypatch.setattr(ops, "move_session", lambda *a, **k: (False, ops.MOVE_NEEDS_SERVICE))
    res = prov.move_sessions(sessions, DEST)
    assert not res.ok and ops.MOVE_NEEDS_SERVICE in res.actions

    res = prov.move_sessions(sessions, DEST, dry_run=True)
    assert res.ok and all(a.startswith("would") for a in res.actions)

    res = prov.move_projects(prov.discover(), DEST, dry_run=True)
    assert res.ok and len(res.actions) == 2
    assert not prov.move_sessions([], DEST).ok


def test_plan_delete_hard_blocked_for_bad_or_unknown_id_even_with_force(prov):
    for bad in ("nope", "ses_doesnotexist", ""):
        plan = prov.plan_delete(bad, force=True)
        assert plan.hard_blocked and plan.blocked_reason == "session not found"
        out = prov.execute_deletes([plan], dry_run=False, force=True)
        assert actions_mod.actions_failed(out)


def test_plan_delete_resolves_prefix_and_flags_live(prov, monkeypatch):
    monkeypatch.setattr(ops, "active_session_ids", lambda r: set())
    monkeypatch.setattr(ops, "service_info", lambda r: None)
    _no_processes(monkeypatch, {A: 77})
    plan = prov.plan_delete("ses_a1")
    assert plan.session_id == A and plan.live and plan.blocked_reason
    assert not plan.hard_blocked
    assert plan.inventory.fragments  # database rows shown, never deletable
    assert not plan.inventory.deletable
    forced = prov.plan_delete(A, force=True)
    assert forced.blocked_reason is None


def test_execute_deletes_reports_ops_failure_as_error_line(prov, monkeypatch):
    monkeypatch.setattr(prov, "merge_live", lambda *a, **k: {})
    plan = prov.plan_delete(A)
    monkeypatch.setattr(
        ops, "delete_session", lambda r, row: (False, "refused: different session")
    )
    out = prov.execute_deletes([plan], dry_run=False)
    assert out == [
        f"error: opencode session delete {short_id(A)} failed: refused: different session"
    ]
    assert actions_mod.actions_failed(out)

    monkeypatch.setattr(ops, "delete_session", lambda r, row: (True, "deleted session"))
    out = prov.execute_deletes([plan], dry_run=False)
    assert not actions_mod.actions_failed(out)
    dry = prov.execute_deletes([plan], dry_run=True)
    assert dry and not actions_mod.actions_failed(dry)


def test_execute_deletes_stops_live_and_aborts_when_stuck(prov, monkeypatch):
    live = LiveSession(A, "interactive", state="open", pid=77)
    monkeypatch.setattr(prov, "merge_live", lambda *a, **k: {A: live})
    plan = prov.plan_delete(A, force=True)
    called: list = []
    monkeypatch.setattr(
        ops, "delete_session", lambda r, row: called.append(row.id) or (True, "ok")
    )
    monkeypatch.setattr(prov, "stop", lambda sid, lv=None: (False, "pid still alive"))
    out = prov.execute_deletes([plan], dry_run=False, force=True)
    assert f"aborted: could not stop live session ({short_id(A)})" in out
    assert not called
    monkeypatch.setattr(prov, "stop", lambda sid, lv=None: (True, "stopped"))
    out = prov.execute_deletes([plan], dry_run=False, force=True)
    assert called == [A] and not actions_mod.actions_failed(out)


def test_project_delete_kept_line_when_live_cannot_stop(prov, monkeypatch):
    live = LiveSession(A, "interactive", state="open", pid=77)
    monkeypatch.setattr(prov, "merge_live", lambda *a, **k: {A: live})
    plan = prov.plan_project_delete("p1", force=True)
    assert [sp.session_id for sp in plan.session_plans] == [B, A]
    monkeypatch.setattr(prov, "stop", lambda sid, lv=None: (False, "pid still alive"))
    deleted: list = []
    monkeypatch.setattr(
        ops, "delete_session", lambda r, row: deleted.append(row.id) or (True, "ok")
    )
    out = prov.execute_project_delete(plan, dry_run=False, force=True)
    assert deleted == [B]
    assert out[-1] == "aborted: a live session could not be stopped; 1 other session(s) deleted"

    by_path = prov.plan_project_delete(CWD)
    assert by_path.slug == "p1"
    missing = prov.plan_project_delete("nope", force=True)
    assert missing.hard_blocked
    assert prov.execute_project_delete(missing, force=True)[0].startswith("aborted")


def test_status_note_variants(prov, tmp_path, monkeypatch):
    from axism.providers.opencode_store.ops import ServiceInfo

    monkeypatch.setattr(ops, "service_info", lambda r: None)
    assert prov.status_note() == "database ok · service not running"
    monkeypatch.setattr(ops, "service_info", lambda r: ServiceInfo(1, "", ""))
    assert prov.status_note() == "database ok · service running"
    assert OpenCodeProvider(config_dir=tmp_path / "none").status_note() == (
        "database not found"
    )
    bad = tmp_path / "bad"
    make_opencode_db(
        bad, sessions=[], projects=[], messages=[],
        drop_columns={"session_v2": {"directory"}},
    )
    assert OpenCodeProvider(config_dir=bad).status_note() == (
        "unsupported OpenCode database layout (aXism supports OpenCode 2)"
    )


def test_detect(prov, tmp_path, monkeypatch):
    assert prov.detect()
    empty = OpenCodeProvider(config_dir=tmp_path / "empty")
    assert not empty.detect()
    monkeypatch.setattr("shutil.which", lambda c, *a, **k: "/bin/opencode")
    assert empty.detect()


def test_missing_db_lists_nothing_quietly(tmp_path, monkeypatch):
    p = OpenCodeProvider(config_dir=tmp_path / "none")

    def boom(*a, **k):
        raise AssertionError("must not run")

    monkeypatch.setattr(ops, "active_session_ids", boom)
    monkeypatch.setattr(ops, "service_info", boom)
    monkeypatch.setattr("axism.providers.opencode.find_agent_sessions", boom)
    assert p.discover() == []
    assert p.merge_live() == {}
    assert federate_discover([p]) == []


def test_cli_providers_shows_opencode_status(tmp_path, monkeypatch, capsys):
    r = tmp_path / "oc" / "opencode"
    make_opencode_db(r, sessions=[], projects=[], messages=[])
    monkeypatch.setattr(ops, "service_info", lambda root: None)
    save_settings(
        Settings(
            active_provider="claude_code",
            providers={"opencode": ProviderPrefs(enabled=True, config_dir=str(r))},
        )
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["providers"])
    assert exc.value.code in (0, None)
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if " opencode " in ln)
    assert "OpenCode" in line and "database ok · service not running" in line
    claude = next(ln for ln in out.splitlines() if " claude_code " in ln)
    assert "(" not in claude


def test_tui_glyph_O():
    from axism.tui.app import _agent_glyph

    assert _agent_glyph("opencode") == "O"
