"""Read OpenCode 2 sessions out of ``opencode.db`` (read-only, always)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta
from axism.providers.opencode_store.paths import db_path

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "session_v2": {
        "id",
        "project_id",
        "parent_id",
        "directory",
        "title",
        "time_created",
        "time_updated",
    },
    "session_message": {"session_id", "type", "seq", "data"},
}


def connect(db: Path) -> sqlite3.Connection:
    """Read-only connection — the OpenCode service owns the write side."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def layout_status(root: Path) -> str:
    """``missing`` (no DB), ``unsupported`` (layout/unreadable), or ``ok``."""
    db = db_path(root)
    if not db.is_file():
        return "missing"
    try:
        con = connect(db)
    except sqlite3.Error:
        return "unsupported"
    try:
        for table, needed in REQUIRED_COLUMNS.items():
            if not needed <= _columns(con, table):
                return "unsupported"
    finally:
        con.close()
    return "ok"


def _text_of(msg_type: str, raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if msg_type == "user":
        text = data.get("text")
        return text if isinstance(text, str) and text.strip() else None
    if msg_type == "assistant":
        content = data.get("content")
        if not isinstance(content, list):
            return None
        parts = [
            c["text"]
            for c in content
            if isinstance(c, dict)
            and c.get("type") == "text"
            and isinstance(c.get("text"), str)
            and c["text"].strip()
        ]
        return "\n".join(parts) if parts else None
    return None


def discover_all(root: Path) -> list[ProjectInfo]:
    """Top-level sessions grouped by ``project_id``."""
    if layout_status(root) != "ok":
        return []
    db = db_path(root)
    try:
        con = connect(db)
    except sqlite3.Error:
        return []
    try:
        cols = _columns(con, "session_v2")
        archived = "time_archived" in cols
        select = (
            "id, project_id, directory, title, time_updated"
            + (", time_archived" if archived else "")
        )
        try:
            sessions = list(
                con.execute(
                    f"SELECT {select} FROM session_v2 WHERE parent_id IS NULL"
                )
            )
            children: dict[str, list[str]] = {}
            for cid, pid in con.execute(
                "SELECT id, parent_id FROM session_v2 WHERE parent_id IS NOT NULL"
            ):
                children.setdefault(pid, []).append(cid)
            sizes = {
                r[0]: int(r[1] or 0)
                for r in con.execute(
                    "SELECT session_id, SUM(LENGTH(COALESCE(data, ''))) "
                    "FROM session_message GROUP BY session_id"
                )
            }
            first: dict[str, str] = {}
            for sid, raw in con.execute(
                "SELECT session_id, data FROM session_message "
                "WHERE type = 'user' ORDER BY session_id, seq"
            ):
                if sid in first:
                    continue
                text = _text_of("user", raw)
                if text:
                    first[sid] = " ".join(text.split())[:240]
        except sqlite3.Error:
            return []
        worktrees: dict[str, str] = {}
        if {"id", "worktree"} <= _columns(con, "project"):
            try:
                worktrees = {
                    r[0]: r[1]
                    for r in con.execute("SELECT id, worktree FROM project")
                    if r[0] and r[1]
                }
            except sqlite3.Error:
                worktrees = {}
    finally:
        con.close()

    projects: dict[str, ProjectInfo] = {}
    for row in sessions:
        sid = str(row["id"])
        pid = str(row["project_id"])
        info = projects.get(pid)
        if info is None:
            info = ProjectInfo(
                slug=pid,
                cwd_guess=worktrees.get(pid) or (row["directory"] or ""),
                path=db,
            )
            projects[pid] = info
        kids = children.get(sid, [])
        title = row["title"]
        info.sessions.append(
            SessionMeta(
                session_id=sid,
                project_slug=pid,
                cwd=row["directory"] or None,
                title=title if isinstance(title, str) and title.strip() else None,
                first_prompt=first.get(sid),
                transcript_path=db,
                mtime=float(row["time_updated"] or 0) / 1000.0,
                size_bytes=sizes.get(sid, 0) + sum(sizes.get(k, 0) for k in kids),
                subagent_count=len(kids),
                entrypoint="archived" if archived and row["time_archived"] else None,
            )
        )
    ordered = list(projects.values())
    for info in ordered:
        info.sessions.sort(key=lambda s: s.mtime, reverse=True)
    ordered.sort(
        key=lambda p: max((s.mtime for s in p.sessions), default=0), reverse=True
    )
    return ordered


@dataclass
class SessionRow:
    id: str
    project_id: str
    parent_id: str | None
    directory: str
    title: str | None
    time_created: int
    time_updated: int
    child_ids: list[str] = field(default_factory=list)


def session_row(root: Path, session_id: str) -> SessionRow | None:
    if layout_status(root) != "ok":
        return None
    try:
        con = connect(db_path(root))
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT id, project_id, parent_id, directory, title, "
            "time_created, time_updated FROM session_v2 WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        kids = [
            r[0]
            for r in con.execute(
                "SELECT id FROM session_v2 WHERE parent_id = ? ORDER BY id",
                (session_id,),
            )
        ]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return SessionRow(
        id=row["id"],
        project_id=row["project_id"],
        parent_id=row["parent_id"],
        directory=row["directory"] or "",
        title=row["title"],
        time_created=int(row["time_created"] or 0),
        time_updated=int(row["time_updated"] or 0),
        child_ids=kids,
    )


def family_map(root: Path) -> dict[str, list[str]]:
    """Top-level session id -> every descendant id (children, grandchildren, …)."""
    if layout_status(root) != "ok":
        return {}
    try:
        con = connect(db_path(root))
    except sqlite3.Error:
        return {}
    try:
        pairs = list(con.execute("SELECT id, parent_id FROM session_v2"))
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    kids: dict[str, list[str]] = {}
    for sid, parent in pairs:
        if parent is not None:
            kids.setdefault(parent, []).append(sid)
    out: dict[str, list[str]] = {}
    for sid, parent in pairs:
        if parent is not None:
            continue
        seen: set[str] = set()
        todo = list(kids.get(sid, []))
        while todo:
            cur = todo.pop()
            if cur in seen or cur == sid:
                continue
            seen.add(cur)
            todo.extend(kids.get(cur, []))
        out[sid] = sorted(seen)
    return out


def transcript_turns(root: Path, session_id: str) -> list[tuple[str, str]]:
    """(role, text) for user and assistant messages, in ``seq`` order."""
    if layout_status(root) != "ok":
        return []
    try:
        con = connect(db_path(root))
    except sqlite3.Error:
        return []
    try:
        rows = list(
            con.execute(
                "SELECT type, data FROM session_message "
                "WHERE session_id = ? AND type IN ('user', 'assistant') "
                "ORDER BY seq",
                (session_id,),
            )
        )
    except sqlite3.Error:
        return []
    finally:
        con.close()
    turns: list[tuple[str, str]] = []
    for row in rows:
        text = _text_of(row["type"], row["data"])
        if text:
            turns.append((row["type"], text))
    return turns


def _model_name(raw: object) -> str | None:
    """Short ``provider/model-id`` from the ``model`` column (JSON or plain text)."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text[0] not in "{[":
            return text
        try:
            raw = json.loads(text)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None

    def pick(*keys: str) -> str | None:
        for key in keys:
            val = raw.get(key)  # type: ignore[union-attr]
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    model = pick("id", "modelID", "model_id", "model")
    if model is None:
        return None
    provider = pick("providerID", "provider_id", "provider")
    if provider and not model.startswith(provider + "/"):
        return f"{provider}/{model}"
    return model


def _num(value: object, kind: type) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return kind(value)


def session_usage(root: Path, session_id: str) -> dict[str, object]:
    """Created time, model, cost and tokens recorded for one session.

    Missing columns or an unreadable database yield an empty dict.
    """
    if layout_status(root) != "ok":
        return {}
    try:
        con = connect(db_path(root))
    except sqlite3.Error:
        return {}
    try:
        cols = _columns(con, "session_v2")
        wanted = [
            c
            for c in ("time_created", "model", "cost", "tokens_input", "tokens_output")
            if c in cols
        ]
        row = con.execute(
            f"SELECT {', '.join(wanted)} FROM session_v2 WHERE id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    if row is None:
        return {}
    data = dict(zip(wanted, row, strict=True))
    out: dict[str, object] = {}
    created = _num(data.get("time_created"), float)
    if created:
        out["created"] = created / 1000.0
    if "model" in data:
        out["model"] = _model_name(data["model"])
    out["cost_usd"] = _num(data.get("cost"), float)
    out["tokens_in"] = _num(data.get("tokens_input"), int)
    out["tokens_out"] = _num(data.get("tokens_output"), int)
    return out
