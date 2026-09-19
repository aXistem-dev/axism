from axism.live import LiveSession, kill_live_session


def test_kill_no_live(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    ok, msg = kill_live_session(
        "11111111-1111-1111-1111-111111111111", use_cli=False
    )
    assert ok is False
    assert "no live" in msg.lower()


def test_kill_interactive_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    calls: list[tuple] = []

    def fake_alive(pid: int) -> bool:
        return pid == 4242 and not any(c[0] == "signaled" for c in calls)

    def fake_signal(pid: int, *, force: bool = False):
        calls.append(("signaled", pid, force))
        return True, f"signaled pid {pid}"

    monkeypatch.setattr("axism.live._pid_alive", fake_alive)
    monkeypatch.setattr("axism.live._signal_pid", fake_signal)

    live = LiveSession(
        session_id="11111111-1111-1111-1111-111111111111",
        kind="interactive",
        state="busy",
        pid=4242,
    )
    ok, msg = kill_live_session(
        live.session_id, live, root=tmp_path, use_cli=False
    )
    assert ok is True
    assert calls and calls[0][1] == 4242
    assert "signaled" in msg


def test_delete_dry_run_would_kill_pid(fake_root, monkeypatch):
    from axism.delete import build_delete_plan, execute_delete
    from axism.live import LiveSession

    sid = "11111111-1111-1111-1111-111111111111"
    live = LiveSession(session_id=sid, kind="interactive", state="busy", pid=9999)

    def fake_merge(root, use_cli=True):
        return {sid: live}

    monkeypatch.setattr("axism.delete.merge_live", fake_merge)
    monkeypatch.setattr("axism.live._pid_alive", lambda pid: pid == 9999)
    plan = build_delete_plan(sid, root=fake_root, force=True, use_cli=False)
    plan.live = live
    actions = execute_delete(
        plan, root=fake_root, dry_run=True, force=True, stop_live=True
    )
    assert any("would stop/kill" in a and "pid=9999" in a for a in actions)


def test_is_killable_requires_alive_pid(monkeypatch):
    from axism.live import LiveSession

    live = LiveSession(
        session_id="11111111-1111-1111-1111-111111111111",
        kind="interactive",
        state="idle",
        pid=1,
    )
    monkeypatch.setattr("axism.live._pid_alive", lambda pid: False)
    assert live.is_killable is False
    monkeypatch.setattr("axism.live._pid_alive", lambda pid: True)
    assert live.is_killable is True
