"""Discover Claude Code projects and session transcripts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from axism.paths import (
    config_root,
    decode_project_slug,
    is_session_uuid,
    path_size,
    projects_dir,
)


@dataclass
class SessionMeta:
    session_id: str
    project_slug: str
    cwd: str | None
    title: str | None
    first_prompt: str | None
    transcript_path: Path
    mtime: float
    size_bytes: int
    has_subdir: bool = False
    subagent_count: int = 0
    has_bridge: bool = False
    entrypoint: str | None = None

    @property
    def display_title(self) -> str:
        return self.title or self.first_prompt or self.session_id

    @property
    def mtime_dt(self) -> datetime:
        return datetime.fromtimestamp(self.mtime, tz=UTC)


@dataclass
class ProjectInfo:
    slug: str
    cwd_guess: str
    path: Path
    sessions: list[SessionMeta] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def total_bytes(self) -> int:
        return sum(s.size_bytes for s in self.sessions)


def _extract_text_content(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
            elif isinstance(block, str) and block.strip():
                parts.append(block.strip())
        if parts:
            return "\n".join(parts)
    return None


def parse_transcript_meta(path: Path, *, max_scan_bytes: int = 2_000_000) -> dict:
    """Stream a transcript JSONL and collect lightweight metadata."""
    meta: dict = {
        "session_id": path.stem,
        "title": None,
        "first_prompt": None,
        "cwd": None,
        "has_bridge": False,
        "entrypoint": None,
    }
    try:
        size = path.stat().st_size
    except OSError:
        return meta

    # Read head fully (up to max), and a tail chunk for late titles
    chunks: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            if size <= max_scan_bytes:
                chunks.append(fh.read())
            else:
                head = fh.read(max_scan_bytes // 2)
                fh.seek(max(0, size - max_scan_bytes // 2))
                tail = fh.read()
                chunks.extend([head, tail])
    except OSError:
        return meta

    text = "\n".join(chunks)
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("sessionId")
        if isinstance(sid, str):
            meta["session_id"] = sid
        t = obj.get("type")
        if t == "ai-title" and isinstance(obj.get("title"), str):
            meta["title"] = obj["title"]
        elif t == "custom-title" and isinstance(obj.get("customTitle"), str):
            meta["title"] = obj["customTitle"]
        elif t == "agent-name" and isinstance(obj.get("agentName"), str):
            if not meta["title"]:
                meta["title"] = obj["agentName"]
        elif t == "bridge-session":
            meta["has_bridge"] = True
        if obj.get("bridgeSessionId"):
            meta["has_bridge"] = True
        if meta["cwd"] is None and isinstance(obj.get("cwd"), str):
            meta["cwd"] = obj["cwd"]
        if meta["entrypoint"] is None and isinstance(obj.get("entrypoint"), str):
            meta["entrypoint"] = obj["entrypoint"]
        if (
            meta["first_prompt"] is None
            and t == "user"
            and not obj.get("isSidechain")
        ):
            prompt = _extract_text_content(obj.get("message"))
            # Skip slash-command-only noise when possible, but keep something
            if prompt:
                meta["first_prompt"] = prompt[:240]
    return meta


def _count_subagents(session_dir: Path) -> int:
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return 0
    return sum(1 for p in sub.glob("*.jsonl") if p.is_file())


def discover_project(slug_dir: Path) -> ProjectInfo:
    slug = slug_dir.name
    info = ProjectInfo(
        slug=slug,
        cwd_guess=decode_project_slug(slug),
        path=slug_dir,
    )
    if not slug_dir.is_dir():
        return info
    for entry in sorted(slug_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".jsonl" and is_session_uuid(entry.stem):
            try:
                st = entry.stat()
            except OSError:
                continue
            parsed = parse_transcript_meta(entry)
            subdir = slug_dir / entry.stem
            has_subdir = subdir.is_dir()
            size = path_size(entry) + (path_size(subdir) if has_subdir else 0)
            info.sessions.append(
                SessionMeta(
                    session_id=parsed.get("session_id") or entry.stem,
                    project_slug=slug,
                    cwd=parsed.get("cwd") or info.cwd_guess,
                    title=parsed.get("title"),
                    first_prompt=parsed.get("first_prompt"),
                    transcript_path=entry,
                    mtime=st.st_mtime,
                    size_bytes=size,
                    has_subdir=has_subdir,
                    subagent_count=_count_subagents(subdir) if has_subdir else 0,
                    has_bridge=bool(parsed.get("has_bridge")),
                    entrypoint=parsed.get("entrypoint"),
                )
            )
    info.sessions.sort(key=lambda s: s.mtime, reverse=True)
    return info


def discover_all(root: Path | None = None) -> list[ProjectInfo]:
    """Scan all project slugs under the config root."""
    root = root or config_root()
    pdir = projects_dir(root)
    if not pdir.is_dir():
        return []
    projects: list[ProjectInfo] = []
    for child in sorted(pdir.iterdir()):
        if child.is_dir() and child.name != "memory":
            proj = discover_project(child)
            # Include empty project dirs so they can be cleaned up
            projects.append(proj)
    projects.sort(key=lambda p: max((s.mtime for s in p.sessions), default=0), reverse=True)
    return projects


def find_session(
    session_id: str, root: Path | None = None
) -> tuple[ProjectInfo, SessionMeta] | None:
    sid = session_id.lower()
    for proj in discover_all(root):
        for sess in proj.sessions:
            if sess.session_id.lower() == sid or sess.session_id.lower().startswith(sid):
                return proj, sess
    return None


def filter_projects_by_cwd(
    projects: list[ProjectInfo], cwd: str | Path
) -> list[ProjectInfo]:
    target = str(Path(cwd).expanduser())
    from axism.paths import encode_project_slug

    slug = encode_project_slug(target)
    return [p for p in projects if p.slug == slug or (p.cwd_guess == target)]
