"""Synthetic OpenCode 2 database builder for tests (never the real database)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SESSION_COLS = (
    "id",
    "project_id",
    "parent_id",
    "directory",
    "title",
    "time_created",
    "time_updated",
    "time_archived",
)
# Optional per-session usage columns; created only when a session row sets one.
USAGE_COLS = ("model", "cost", "tokens_input", "tokens_output")
MESSAGE_COLS = ("session_id", "type", "seq", "data")
PROJECT_COLS = ("id", "worktree")


def make_opencode_db(
    root: Path,
    *,
    sessions: list[dict],
    projects: list[dict],
    messages: list[dict],
    drop_columns: dict[str, set[str]] | None = None,
) -> Path:
    """Create ``root/opencode.db`` with the minimal OpenCode 2 tables."""
    drop = drop_columns or {}
    root.mkdir(parents=True, exist_ok=True)
    db = root / "opencode.db"
    con = sqlite3.connect(db)
    try:
        specs = {
            "session_v2": SESSION_COLS,
            "session_message": MESSAGE_COLS,
            "project": PROJECT_COLS,
        }
        rows = {
            "session_v2": sessions,
            "session_message": messages,
            "project": projects,
        }
        if any(c in row for row in sessions for c in USAGE_COLS):
            specs["session_v2"] = SESSION_COLS + USAGE_COLS
        for table, cols in specs.items():
            kept = [c for c in cols if c not in drop.get(table, set())]
            con.execute(f"CREATE TABLE {table} ({', '.join(kept)})")
            for row in rows[table]:
                values = []
                for c in kept:
                    v = row.get(c)
                    if c in ("data", "model") and not isinstance(
                        v, (str, int, float, type(None))
                    ):
                        v = json.dumps(v)
                    values.append(v)
                marks = ", ".join("?" for _ in kept)
                con.execute(
                    f"INSERT INTO {table} ({', '.join(kept)}) VALUES ({marks})",
                    values,
                )
        con.execute("CREATE TABLE credential (id, secret)")
        con.execute("INSERT INTO credential VALUES ('c1', 'not-a-real-secret')")
        con.commit()
    finally:
        con.close()
    return db


# Server response shapes as OpenCode 2.1.4 prints them through ``opencode api``
# (ids and paths synthetic).


def session_info(
    session_id: str,
    project_id: str,
    directory: str,
    *,
    created: int,
    updated: int | None = None,
    title: str = "Demo",
) -> dict:
    return {
        "id": session_id,
        "projectID": project_id,
        "cost": 0,
        "tokens": {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
        "time": {"created": created, "updated": updated or created},
        "title": title,
        "location": {"directory": directory},
    }


def server_info(pid: int) -> dict:
    return {
        "version": "2.1.4",
        "pid": pid,
        "urls": ["http://127.0.0.1:4096"],
        "paths": {"tmp": "/tmp/opencode"},
        "capabilities": {"persistentPty": True},
    }


def not_found(session_id: str) -> dict:
    return {
        "_tag": "SessionNotFoundError",
        "sessionID": session_id,
        "message": f"Session not found: {session_id}",
    }
