"""What an OpenCode 2 session occupies on disk: rows in ``opencode.db`` only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from axism.fragments import Fragment, FragmentInventory
from axism.providers.opencode_store.discover import connect, family_map
from axism.providers.opencode_store.paths import db_path


def _message_bytes(root: Path, session_id: str) -> int:
    ids = [session_id, *family_map(root).get(session_id, [])]
    try:
        con = connect(db_path(root))
    except sqlite3.Error:
        return 0
    try:
        marks = ", ".join("?" for _ in ids)
        r = con.execute(
            "SELECT SUM(LENGTH(COALESCE(data, ''))) FROM session_message "
            f"WHERE session_id IN ({marks})",
            ids,
        ).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        con.close()
    return int(r[0] or 0) if r else 0


def collect_fragments(
    root: Path, session_id: str, *, project_slug: str | None = None
) -> FragmentInventory:
    inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
    db = db_path(root)
    if db.is_file():
        # opencode.db is in PROTECTED_NAMES: shown for size, never unlinked.
        inv.fragments.append(
            Fragment(
                kind="session_rows",
                path=db.resolve(),
                size_bytes=_message_bytes(root, session_id),
            )
        )
    return inv
