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
        for table, cols in specs.items():
            kept = [c for c in cols if c not in drop.get(table, set())]
            con.execute(f"CREATE TABLE {table} ({', '.join(kept)})")
            for row in rows[table]:
                values = []
                for c in kept:
                    v = row.get(c)
                    if c == "data" and not isinstance(v, (str, type(None))):
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
