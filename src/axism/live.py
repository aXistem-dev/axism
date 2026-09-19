"""Live / background session enrichment from filesystem and Claude CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from axism.paths import config_root


@dataclass
class LiveSession:
    session_id: str
    kind: str  # interactive | background
    name: str | None = None
    cwd: str | None = None
    state: str | None = None  # busy, working, done, blocked, failed, …
    pid: int | None = None
    bridge_session_id: str | None = None
    daemon_short: str | None = None
    source: str = "filesystem"  # filesystem | claude_cli

    @property
    def is_live(self) -> bool:
        """Apparent liveness from registry/CLI (may include stale PIDs)."""
        if self.kind == "interactive":
            return self.state in {"busy", "idle", None} and self.pid is not None
        return self.state in {"working", "blocked", "running"}

    @property
    def is_killable(self) -> bool:
        """True only when a stop/kill would affect a real process or active job."""
        if self.state in {"exited", "done", "failed"}:
            return False
        if self.kind == "interactive":
            return self.pid is not None and _pid_alive(self.pid)
        # background: active job states only (do not require a local pid)
        if self.state in {"working", "blocked", "running"}:
            return True
        if self.pid is not None and _pid_alive(self.pid):
            return True
        return False

    @property
    def is_remote(self) -> bool:
        return bool(self.bridge_session_id)


def _pid_alive(pid: int) -> bool:
    try:
        os_kill = __import__("os").kill
        os_kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def load_filesystem_live(root: Path | None = None) -> dict[str, LiveSession]:
    """Read ~/.claude/sessions/<pid>.json registry."""
    root = root or config_root()
    out: dict[str, LiveSession] = {}
    sdir = root / "sessions"
    if not sdir.is_dir():
        return out
    for entry in sdir.iterdir():
        if not entry.name.endswith(".json"):
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        if not isinstance(sid, str):
            continue
        pid = data.get("pid")
        alive = isinstance(pid, int) and _pid_alive(pid)
        status = data.get("status") if alive else "exited"
        out[sid] = LiveSession(
            session_id=sid,
            kind=data.get("kind") or "interactive",
            name=data.get("name"),
            cwd=data.get("cwd"),
            state=status,
            pid=pid if isinstance(pid, int) else None,
            bridge_session_id=data.get("bridgeSessionId"),
            source="filesystem",
        )
    return out


def load_job_states(root: Path | None = None) -> dict[str, LiveSession]:
    """Read jobs/*/state.json keyed by full sessionId."""
    root = root or config_root()
    out: dict[str, LiveSession] = {}
    jobs = root / "jobs"
    if not jobs.is_dir():
        return out
    for d in jobs.iterdir():
        state_file = d / "state.json"
        if not state_file.is_file():
            continue
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId") or data.get("resumeSessionId")
        if not isinstance(sid, str):
            continue
        out[sid] = LiveSession(
            session_id=sid,
            kind="background",
            name=data.get("name"),
            cwd=data.get("cwd"),
            state=data.get("state"),
            daemon_short=data.get("daemonShort") or d.name,
            bridge_session_id=data.get("bridgeSessionId"),
            source="filesystem",
        )
    return out


def load_claude_agents_json(timeout: float = 8.0) -> dict[str, LiveSession]:
    """Call ``claude agents --json --all`` when available."""
    if not shutil.which("claude"):
        return {}
    try:
        proc = subprocess.run(
            ["claude", "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, LiveSession] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = item.get("sessionId")
        if not isinstance(sid, str):
            continue
        kind = item.get("kind") or "background"
        state = item.get("state") or item.get("status")
        out[sid] = LiveSession(
            session_id=sid,
            kind=kind,
            name=item.get("name"),
            cwd=item.get("cwd"),
            state=state,
            pid=item.get("pid") if isinstance(item.get("pid"), int) else None,
            daemon_short=item.get("id") if isinstance(item.get("id"), str) else None,
            source="claude_cli",
        )
    return out


def merge_live(root: Path | None = None, *, use_cli: bool = True) -> dict[str, LiveSession]:
    """Merge filesystem + optional CLI live info. CLI wins on conflicts."""
    merged: dict[str, LiveSession] = {}
    merged.update(load_job_states(root))
    merged.update(load_filesystem_live(root))
    if use_cli:
        for sid, live in load_claude_agents_json().items():
            merged[sid] = live
    return merged


def stop_background(session_or_short: str, *, retries: int = 3) -> tuple[bool, str]:
    """Try ``claude stop`` / ``kill`` / ``rm`` for a background session.

    Claude's daemon sometimes returns a transient “restarting” error for blocked
    jobs; we retry a few times before giving up.
    """
    import time

    if not shutil.which("claude"):
        return False, "claude CLI not found on PATH"
    short = session_or_short[:8]
    messages: list[str] = []
    cmds = (
        ["claude", "stop", short],
        ["claude", "kill", short],
        ["claude", "rm", short],
    )
    for attempt in range(max(1, retries)):
        for cmd in cmds:
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, check=False
                )
                out = (proc.stdout or proc.stderr or "").strip()
                messages.append(
                    f"{' '.join(cmd)} -> rc={proc.returncode} {out[:200]}"
                )
                if proc.returncode == 0:
                    return True, "\n".join(messages)
            except (OSError, subprocess.TimeoutExpired) as exc:
                messages.append(f"{' '.join(cmd)} failed: {exc}")
        if attempt + 1 < retries:
            time.sleep(0.4 * (attempt + 1))
    return False, "\n".join(messages) or "stop/kill/rm failed"


def slash_stop(
    session_id: str,
    live: LiveSession | None = None,
    *,
    root: Path | None = None,
    retries: int = 3,
) -> tuple[bool, str]:
    """Stop a background session the same way as typing ``/stop`` while attached.

    Shell equivalent is ``claude stop <short-id>`` (transcript kept).
    Interactive sessions are not supported — ``/stop`` only exists on background.
    """
    import time

    root = root or config_root()
    if live is None:
        live = merge_live(root, use_cli=True).get(session_id)
    if live is None:
        return False, f"no live record for {session_id}"
    if live.kind != "background":
        return (
            False,
            (
                "Stop (/stop) only applies to background sessions — Open (o) an "
                "interactive session and interrupt it there"
            ),
        )
    if not shutil.which("claude"):
        return False, "claude CLI not found on PATH"
    short = live.daemon_short or session_id[:8]
    messages: list[str] = []
    for attempt in range(max(1, retries)):
        try:
            proc = subprocess.run(
                ["claude", "stop", short],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            messages.append(f"claude stop {short} -> rc={proc.returncode} {out[:240]}")
            if proc.returncode == 0:
                return True, "\n".join(messages)
        except (OSError, subprocess.TimeoutExpired) as exc:
            messages.append(f"claude stop {short} failed: {exc}")
        if attempt + 1 < retries:
            time.sleep(0.4 * (attempt + 1))
    return False, "\n".join(messages) or "claude stop failed"


def clear_local_job(session_id: str, *, root: Path | None = None) -> tuple[bool, str]:
    """Mark/remove local ``jobs/<short>/`` so aXism no longer treats it as live.

    Does not stop a remote Claude daemon by itself; use after CLI stop fails for
    stuck blocked jobs with no local PID.
    """
    root = root or config_root()
    short = session_id[:8]
    job_dir = root / "jobs" / short
    if not job_dir.is_dir():
        return False, f"no local job dir jobs/{short}"
    state_path = job_dir / "state.json"
    messages: list[str] = []
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["state"] = "done"
                data["detail"] = (data.get("detail") or "") + " [cleared by axism]"
                state_path.write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )
                messages.append(f"marked jobs/{short}/state.json as done")
        except (OSError, json.JSONDecodeError) as exc:
            messages.append(f"could not rewrite state.json: {exc}")
    try:
        # Drop timeline/tmp noise; keep state.json so Claude can still see history
        for name in ("timeline.jsonl", "tmp"):
            p = job_dir / name
            if p.is_file():
                p.unlink()
                messages.append(f"removed {name}")
            elif p.is_dir():
                shutil.rmtree(p)
                messages.append(f"removed {name}/")
    except OSError as exc:
        messages.append(f"cleanup error: {exc}")
    return bool(messages), "\n".join(messages) or "nothing cleared"


def _signal_pid(pid: int, *, force: bool = False) -> tuple[bool, str]:
    """Send SIGTERM (or SIGKILL if force) to a process."""
    import os
    import signal
    import time

    if not _pid_alive(pid):
        return True, f"pid {pid} already exited"
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError as exc:
        return False, f"kill({pid}) failed: {exc}"
    # Brief wait for graceful exit
    for _ in range(20):
        if not _pid_alive(pid):
            return True, f"signaled pid {pid} ({'KILL' if force else 'TERM'})"
        time.sleep(0.05)
    if not force:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            return False, f"SIGKILL {pid} failed: {exc}"
        time.sleep(0.1)
        if not _pid_alive(pid):
            return True, f"signaled pid {pid} (TERM then KILL)"
        return False, f"pid {pid} still alive after KILL"
    return False, f"pid {pid} still alive"


def kill_live_session(
    session_id: str,
    live: LiveSession | None = None,
    *,
    root: Path | None = None,
    use_cli: bool = True,
    clear_local_on_fail: bool = True,
) -> tuple[bool, str]:
    """Stop a live interactive or background Claude Code session.

    Background: ``claude stop`` / ``kill`` / ``rm`` (with retries).
    Interactive: SIGTERM/SIGKILL on the registered PID.
    If the Claude daemon refuses a blocked no-PID job, optionally clear local
    ``jobs/<short>/`` state so aXism stops listing it as killable.
    """
    root = root or config_root()
    if live is None:
        live = merge_live(root, use_cli=use_cli).get(session_id)

    if live is None:
        return False, f"no live/background process found for {session_id}"

    messages: list[str] = []
    ok = False

    if live.kind == "background" or (
        live.daemon_short and live.state in {"working", "blocked", "running"}
    ):
        target = live.daemon_short or session_id
        ok, msg = stop_background(target)
        messages.append(msg)
        if not ok and live.daemon_short and live.daemon_short != session_id[:8]:
            ok2, msg2 = stop_background(session_id)
            messages.append(msg2)
            ok = ok or ok2

    if live.pid is not None and _pid_alive(live.pid):
        ok_pid, msg_pid = _signal_pid(live.pid, force=False)
        messages.append(msg_pid)
        ok = ok or ok_pid

    if (
        not ok
        and clear_local_on_fail
        and live.kind == "background"
        and (live.pid is None or not _pid_alive(live.pid))
    ):
        cleared, cmsg = clear_local_job(session_id, root=root)
        messages.append(cmsg)
        if cleared:
            # Local view cleared; Claude agents may still list it until daemon drops it
            ok = True
            messages.append(
                "cleared local job state (Claude daemon may still list it until "
                "it recovers — try Kill again or restart the background service)"
            )

    if not ok and live.pid is None and live.kind != "background":
        return False, "session is not live (no pid / not a running background job)"

    # Best-effort: remove stale live registry files for this session
    sdir = root / "sessions"
    if sdir.is_dir() and live.pid is not None and not _pid_alive(live.pid):
        for entry in list(sdir.glob(f"{live.pid}.*")) + (
            [sdir / f"{live.pid}.json"] if (sdir / f"{live.pid}.json").exists() else []
        ):
            try:
                if entry.exists():
                    entry.unlink()
                    messages.append(f"removed {entry.name}")
            except OSError as exc:
                messages.append(f"could not remove {entry.name}: {exc}")

    if not ok:
        # Prefer the most useful Claude CLI line for the UI
        hint = next(
            (
                line
                for line in reversed("\n".join(messages).splitlines())
                if "couldn't" in line.lower() or "no job" in line.lower()
            ),
            messages[-1] if messages else "kill failed",
        )
        return False, hint if not messages else "\n".join(messages)

    return True, "\n".join(messages) if messages else "killed"


def purge_project_cli(
    path: str | None = None, *, dry_run: bool = True, all_projects: bool = False
) -> tuple[int, str]:
    """Shell out to ``claude project purge``."""
    if not shutil.which("claude"):
        return 1, "claude CLI not found on PATH"
    cmd = ["claude", "project", "purge"]
    if all_projects:
        cmd.append("--all")
    elif path:
        cmd.append(path)
    else:
        return 1, "path or --all required"
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("-y")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def open_command(
    session_id: str,
    *,
    cwd: str | None = None,
    live: LiveSession | None = None,
) -> tuple[list[str], str | None]:
    """Build argv to hand off to Claude Code.

    Active background jobs use ``claude attach <short>`` (wakes blocked daemons).
    Everything else uses ``claude --resume <uuid>``.
    """
    if not shutil.which("claude"):
        raise FileNotFoundError("claude CLI not found on PATH")
    if live is not None and live.kind == "background":
        if live.state in {"working", "blocked", "running"} or live.is_killable:
            short = live.daemon_short or session_id[:8]
            return ["claude", "attach", short], cwd
    return ["claude", "--resume", session_id], cwd


def resume_command(session_id: str, *, cwd: str | None = None) -> tuple[list[str], str | None]:
    """Build ``claude --resume <id>`` argv (always resume, never attach)."""
    if not shutil.which("claude"):
        raise FileNotFoundError("claude CLI not found on PATH")
    return ["claude", "--resume", session_id], cwd


def exec_open(
    session_id: str,
    *,
    cwd: str | None = None,
    live: LiveSession | None = None,
) -> None:
    """Replace this process with ``claude attach`` or ``claude --resume``.

    Never returns on success. Raises FileNotFoundError / OSError on failure.
    """
    import os
    from pathlib import Path

    argv, workdir = open_command(session_id, cwd=cwd, live=live)
    if workdir:
        path = Path(workdir).expanduser()
        if path.is_dir():
            os.chdir(path)
    os.execvp(argv[0], argv)


def exec_resume(session_id: str, *, cwd: str | None = None) -> None:
    """Replace this process with ``claude --resume <session_id>``."""
    exec_open(session_id, cwd=cwd, live=None)
