"""Helpers shared by filesystem-backed providers (Cursor, Hermes, …).

Claude Code keeps its own bespoke delete/stop logic in the top-level modules;
newer backends reuse these generic building blocks instead.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from axism.delete import DeletePlan
from axism.live import LiveSession


def remove_path(path: Path) -> str:
    """Delete a file or directory tree, returning a log line."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return f"removed file {path}"
    if path.is_dir():
        shutil.rmtree(path)
        return f"removed dir {path}"
    return f"missing {path}"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(pid: int, *, grace: float = 1.0) -> tuple[bool, str]:
    """SIGTERM then SIGKILL a process, waiting briefly in between."""
    if not pid_alive(pid):
        return True, f"pid {pid} already exited"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, f"kill({pid}) failed: {exc}"
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True, f"stopped pid {pid} (TERM)"
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        return False, f"SIGKILL {pid} failed: {exc}"
    time.sleep(0.1)
    if pid_alive(pid):
        return False, f"pid {pid} still alive after KILL"
    return True, f"stopped pid {pid} (TERM then KILL)"


def process_table() -> list[tuple[int, str]]:
    """``(pid, command line)`` for every visible process.

    Reads ``/proc`` on Linux and falls back to ``ps`` elsewhere (macOS).
    """
    proc = Path("/proc")
    rows: list[tuple[int, str]] = []
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if not raw:
                continue
            cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
            rows.append((int(entry.name), cmd))
        return rows
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return rows
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, args = line.partition(" ")
        if pid_text.isdigit():
            rows.append((int(pid_text), args.strip()))
    return rows


def exec_with_cwd(argv: list[str], workdir: str | None) -> None:
    """Chdir into ``workdir`` (if it exists) then replace this process with ``argv``."""
    if workdir:
        path = Path(workdir).expanduser()
        if path.is_dir():
            os.chdir(path)
    os.execvp(argv[0], argv)


def find_agent_sessions(
    session_ids: set[str], binaries: set[str]
) -> dict[str, int]:
    """Session id → pid of an agent process working on it.

    Matching needs both the agent binary and the id in one command line. aXism's
    own process is skipped: it carries the id (and often the backend name) in
    its argv while listing or showing a session.
    """
    if not session_ids:
        return {}
    own = os.getpid()
    found: dict[str, int] = {}
    for pid, cmdline in process_table():
        if pid == own:
            continue
        tokens = cmdline.split()
        names = {os.path.basename(token) for token in tokens}
        if "axism" in names:
            continue
        if not (names & binaries):
            continue
        for session_id in session_ids:
            if session_id in cmdline:
                found[session_id] = pid
    return found


def execute_fragment_deletes(
    plans: list[DeletePlan],
    *,
    dry_run: bool = True,
    force: bool = False,
    stop_live: bool = True,
    stop_fn=None,
) -> list[str]:
    """Remove every deletable fragment path in ``plans``.

    ``stop_fn(session_id, live) -> (ok, message)`` is called first for live
    sessions when ``stop_live`` is set.
    """
    actions: list[str] = []
    if not plans:
        return ["nothing to delete"]

    blocked = [p for p in plans if p.blocked_reason and not force]
    if blocked:
        return [f"aborted {p.session_id}: {p.blocked_reason}" for p in blocked]

    for plan in plans:
        short = plan.session_id[:8]
        live: LiveSession | None = plan.live
        if stop_live and live is not None and live.is_killable and stop_fn is not None:
            if dry_run:
                actions.append(f"{short}: would stop live session (state={live.state})")
            else:
                ok, msg = stop_fn(plan.session_id, live)
                actions.append(f"{short}: stop: {msg}")
                if not ok and not force:
                    actions.append(f"{short}: aborted: could not stop live session")
                    continue

        for frag in plan.inventory.deletable:
            if dry_run:
                actions.append(
                    f"would remove [{frag.kind}] {frag.path} ({frag.size_bytes} B)"
                )
                continue
            try:
                actions.append(remove_path(frag.path))
            except OSError as exc:
                actions.append(f"error removing {frag.path}: {exc}")

    return actions
