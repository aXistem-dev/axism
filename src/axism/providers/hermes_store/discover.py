"""Read interactive Hermes sessions out of ``state.db``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta
from axism.providers.hermes_store.paths import DEFAULT_SOURCES, state_db

UNSCOPED_SLUG = "_unscoped"
UNSCOPED_LABEL = "(no workspace)"

# Columns we use when present; Hermes adds columns over time, so anything
# missing from an older schema is simply skipped.
WANTED_COLUMNS = (
    "id",
    "source",
    "title",
    "display_name",
    "cwd",
    "git_repo_root",
    "started_at",
    "last_activity_at",
    "message_count",
    "pinned",
    "profile_name",
)


def connect(db: Path) -> sqlite3.Connection:
    """Read-only connection — the gateway may hold the write lock."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _message_sizes(con: sqlite3.Connection) -> dict[str, int]:
    try:
        rows = con.execute(
            "SELECT session_id, SUM(LENGTH(COALESCE(content, ''))) "
            "FROM messages GROUP BY session_id"
        )
    except sqlite3.Error:
        return {}
    return {row[0]: int(row[1] or 0) for row in rows if row[0]}


def _first_prompts(con: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = con.execute(
            "SELECT m.session_id, m.content FROM messages m "
            "JOIN (SELECT session_id, MIN(id) AS first_id FROM messages "
            "      WHERE role = 'user' GROUP BY session_id) f "
            "ON m.id = f.first_id"
        )
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for session_id, content in rows:
        if session_id and isinstance(content, str) and content.strip():
            out[session_id] = " ".join(content.split())[:240]
    return out


def discover_all(
    root: Path, *, sources: Iterable[str] = DEFAULT_SOURCES
) -> list[ProjectInfo]:
    """Interactive sessions grouped by ``git_repo_root`` or ``cwd``."""
    db = state_db(root)
    if not db.is_file():
        return []
    try:
        con = connect(db)
    except sqlite3.Error:
        return []

    try:
        available = _columns(con, "sessions")
        if not available:
            return []
        selected = [c for c in WANTED_COLUMNS if c in available]
        if "id" not in selected:
            return []

        where: list[str] = []
        params: list[object] = []
        if "archived" in available:
            where.append("COALESCE(archived, 0) = 0")
        if "hidden" in available:
            where.append("COALESCE(hidden, 0) = 0")
        source_list = [s for s in sources]
        if "source" in available and source_list:
            placeholders = ", ".join("?" for _ in source_list)
            where.append(f"source IN ({placeholders})")
            params.extend(source_list)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        query = f"SELECT {', '.join(selected)} FROM sessions{clause}"

        try:
            rows = list(con.execute(query, params))
        except sqlite3.Error:
            return []
        sizes = _message_sizes(con)
        prompts = _first_prompts(con)
    finally:
        con.close()

    projects: dict[str, ProjectInfo] = {}
    for row in rows:
        data = dict(row)
        session_id = str(data.get("id") or "").strip()
        if not session_id:
            continue
        workspace = (data.get("git_repo_root") or data.get("cwd") or "").strip()
        slug = workspace or UNSCOPED_SLUG
        info = projects.get(slug)
        if info is None:
            info = ProjectInfo(
                slug=slug,
                cwd_guess=workspace or UNSCOPED_LABEL,
                path=db,
            )
            projects[slug] = info

        title = data.get("title") or data.get("display_name")
        mtime = data.get("last_activity_at") or data.get("started_at") or 0.0
        info.sessions.append(
            SessionMeta(
                session_id=session_id,
                project_slug=slug,
                cwd=workspace or None,
                title=title if isinstance(title, str) and title.strip() else None,
                first_prompt=prompts.get(session_id),
                transcript_path=db,
                mtime=float(mtime or 0.0),
                size_bytes=sizes.get(session_id, 0),
                entrypoint=str(data.get("source") or "") or None,
            )
        )

    ordered = list(projects.values())
    for info in ordered:
        info.sessions.sort(key=lambda s: s.mtime, reverse=True)
    ordered.sort(
        key=lambda p: max((s.mtime for s in p.sessions), default=0), reverse=True
    )
    return ordered


def session_exists(root: Path, session_id: str) -> bool:
    db = state_db(root)
    if not db.is_file():
        return False
    try:
        con = connect(db)
    except sqlite3.Error:
        return False
    try:
        row = con.execute(
            "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return row is not None
