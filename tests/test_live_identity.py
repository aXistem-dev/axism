"""Live-record identity: a recycled pid must never be treated as the session."""

import json
from pathlib import Path

from axism.live import kill_live_session, load_filesystem_live, merge_live

SID = "11111111-1111-1111-1111-111111111111"


def _record(root: Path, pid: int, **extra) -> None:
    sdir = root / "sessions"
    sdir.mkdir(exist_ok=True)
    data = {"pid": pid, "sessionId": SID, "status": "busy", "kind": "interactive"}
    data.update(extra)
    (sdir / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def _fake_kill(monkeypatch, alive_pids: set[int]) -> list[tuple[int, int]]:
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if pid not in alive_pids:
            raise ProcessLookupError(pid)
        if sig != 0:
            sent.append((pid, sig))

    monkeypatch.setattr("axism.live.os.kill", fake_kill)
    return sent


def test_read_proc_start_parses_field_22(monkeypatch):
    from axism import live

    fields_after = ["S"] + ["0"] * 18 + ["987654"] + ["0"] * 5
    text = "42 (we) ird)) " + " ".join(fields_after)
    monkeypatch.setattr(live.Path, "read_text", lambda self, **kw: text)
    assert live._read_proc_start(42) == "987654"


def test_procstart_mismatch_is_not_live_and_never_signalled(tmp_path, monkeypatch):
    _record(tmp_path, 4242, procStart="100")
    sent = _fake_kill(monkeypatch, {4242})
    monkeypatch.setattr("axism.live._read_proc_start", lambda pid: "999")
    live = load_filesystem_live(tmp_path)[SID]
    assert live.state == "exited"
    assert live.pid is None
    assert not live.is_live and not live.is_killable
    ok, _msg = kill_live_session(SID, live, root=tmp_path, use_cli=False)
    assert ok is False
    assert sent == []
    assert (tmp_path / "sessions" / "4242.json").is_file()


def test_procstart_match_is_live(tmp_path, monkeypatch):
    _record(tmp_path, 4242, procStart="100")
    _fake_kill(monkeypatch, {4242})
    monkeypatch.setattr("axism.live._read_proc_start", lambda pid: "100")
    live = load_filesystem_live(tmp_path)[SID]
    assert live.state == "busy" and live.pid == 4242 and live.is_killable


def test_no_procstart_or_unreadable_keeps_pid_check(tmp_path, monkeypatch):
    _record(tmp_path, 4242)
    _fake_kill(monkeypatch, {4242})
    monkeypatch.setattr("axism.live._read_proc_start", lambda pid: "999")
    assert load_filesystem_live(tmp_path)[SID].state == "busy"
    _record(tmp_path, 4242, procStart="100")
    monkeypatch.setattr("axism.live._read_proc_start", lambda pid: None)
    assert load_filesystem_live(tmp_path)[SID].state == "busy"


def test_duplicate_records_prefer_alive_then_newest(tmp_path, monkeypatch):
    _record(tmp_path, 1111, updatedAt=900)  # dead but newest
    _record(tmp_path, 2222, updatedAt=100)  # alive
    _fake_kill(monkeypatch, {2222})
    assert load_filesystem_live(tmp_path)[SID].pid == 2222
    _record(tmp_path, 3333, updatedAt=500)  # also alive, newer
    _fake_kill(monkeypatch, {2222, 3333})
    assert load_filesystem_live(tmp_path)[SID].pid == 3333
    assert merge_live(tmp_path, use_cli=False)[SID].pid == 3333


def _live_plan(root: Path, monkeypatch, force: bool):
    from axism.delete import build_delete_plan

    _record(root, 4242, status="idle")
    monkeypatch.setattr("axism.live._pid_alive", lambda pid: pid == 4242)
    return build_delete_plan(SID, root=root, force=True, use_cli=False)


def test_failed_stop_aborts_delete_even_with_force(fake_root, monkeypatch):
    from axism.delete import (
        build_project_delete_plan,
        execute_delete,
        execute_deletes,
        execute_project_delete,
    )

    transcript = fake_root / "projects" / "-home-alice-src-demo" / f"{SID}.jsonl"
    monkeypatch.setattr(
        "axism.delete.kill_live_session", lambda *a, **k: (False, "denied")
    )
    for force in (False, True):
        plan = _live_plan(fake_root, monkeypatch, force)
        assert plan.live and plan.live.is_killable
        one = execute_delete(plan, root=fake_root, dry_run=False, force=force)
        assert any(a.startswith("aborted: could not stop live session") for a in one)
        many = execute_deletes([plan], root=fake_root, dry_run=False, force=force)
        assert any(a.startswith("aborted: could not stop live session") for a in many)
        assert transcript.is_file()
        assert (fake_root / "sessions" / "4242.json").is_file()

    pplan = build_project_delete_plan(
        "-home-alice-src-demo", root=fake_root, force=True, use_cli=False
    )
    acts = execute_project_delete(pplan, root=fake_root, dry_run=False, force=True)
    assert any(a.startswith("aborted: could not stop live session") for a in acts)
    assert transcript.is_file()
