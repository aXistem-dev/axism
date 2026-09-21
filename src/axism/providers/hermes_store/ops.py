"""Hermes mutations, driven through the ``hermes`` CLI.

The session store is a live SQLite database with a full-text index and a
gateway writing to it, so aXism never edits rows directly: it calls the same
commands a user would.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BINARY = "hermes"


def cli_available() -> bool:
    return shutil.which(BINARY) is not None


def run_hermes(
    root: Path, args: list[str], *, timeout: float = 60.0
) -> tuple[int, str]:
    """Run ``hermes …`` against ``root`` as HERMES_HOME."""
    if not cli_available():
        return 1, f"{BINARY} CLI not found on PATH"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(root)
    try:
        proc = subprocess.run(
            [BINARY, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{BINARY} {' '.join(args)} failed: {exc}"
    output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode, output


def rename_session(root: Path, session_id: str, title: str) -> tuple[bool, str]:
    clean = " ".join(title.split()).strip()
    if not clean:
        return False, "title must not be empty"
    if len(clean) > 200:
        return False, "title too long (max 200 characters)"
    rc, out = run_hermes(root, ["sessions", "rename", session_id, clean])
    if rc != 0:
        return False, out or f"hermes sessions rename failed for {session_id}"
    return True, f"renamed to {clean!r}"


def delete_session(root: Path, session_id: str) -> tuple[bool, str]:
    rc, out = run_hermes(root, ["sessions", "delete", session_id, "--yes"])
    if rc != 0:
        return False, out or f"hermes sessions delete failed for {session_id}"
    return True, out or f"deleted session {session_id}"


def resume_argv(session_id: str) -> list[str]:
    return [BINARY, "--resume", session_id]
