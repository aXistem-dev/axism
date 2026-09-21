"""Build aXism projects/sessions from Cursor's two chat stores."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta
from axism.paths import path_size
from axism.providers.cursor_store.paths import (
    NON_PROJECT_DIRS,
    TITLE_SIDECAR,
    TRANSCRIPTS_DIRNAME,
    bucket_to_cwd,
    chats_dir,
    cwd_bucket,
    decode_slug,
    is_chat_id,
    projects_dir,
    slugify_path,
)

# Agent transcripts wrap the real question in <user_query>; prompts also carry
# <timestamp> and similar context tags we do not want in a title.
_USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

KIND_AGENT = "agent"
KIND_CLI = "cli"
KIND_BOTH = "agent+cli"


def _clean(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def _first_user_prompt(jsonl: Path, *, max_lines: int = 400) -> str | None:
    try:
        handle = jsonl.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for count, line in enumerate(handle):
            if count >= max_lines:
                break
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("role") != "user":
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                text: str | None = None
                if isinstance(block, str):
                    text = block
                elif isinstance(block, dict) and block.get("type") == "text":
                    raw = block.get("text")
                    text = raw if isinstance(raw, str) else None
                if not text:
                    continue
                match = _USER_QUERY_RE.search(text)
                if match:
                    return _clean(match.group(1))
                stripped = _clean(_TAG_RE.sub(" ", text))
                if stripped:
                    return stripped
    return None


def _sidecar_title(conv_dir: Path) -> str | None:
    path = conv_dir / TITLE_SIDECAR
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    title = data.get("title") if isinstance(data, dict) else None
    return title if isinstance(title, str) and title.strip() else None


def read_cli_meta(db: Path) -> dict:
    """Decode the hex-encoded JSON blob in a CLI chat's ``meta`` table."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute("SELECT value FROM meta WHERE key = '0'").fetchone()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    if not row or row[0] is None:
        return {}
    value = row[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    try:
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]+", value):
            value = bytes.fromhex(value).decode("utf-8", "replace")
        data = json.loads(value)
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cli_meta_name(db: Path, name: str) -> tuple[bool, str]:
    """Set the chat title inside a CLI chat's ``store.db``."""
    data = read_cli_meta(db)
    if not data:
        return False, f"could not read chat metadata in {db}"
    data["name"] = name
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8").hex()
    try:
        con = sqlite3.connect(db, timeout=5.0)
        with con:
            con.execute("UPDATE meta SET value = ? WHERE key = '0'", (payload,))
        con.close()
    except sqlite3.Error as exc:
        return False, f"could not write chat metadata: {exc}"
    return True, f"renamed to {name!r}"


def _agent_session(conv_dir: Path, slug: str, cwd: str) -> SessionMeta | None:
    chat_id = conv_dir.name
    transcript = conv_dir / f"{chat_id}.jsonl"
    if not transcript.is_file():
        candidates = sorted(conv_dir.glob("*.jsonl"))
        if not candidates:
            return None
        transcript = candidates[0]
    try:
        stat = transcript.stat()
    except OSError:
        return None
    subagents = conv_dir / "subagents"
    return SessionMeta(
        session_id=chat_id,
        project_slug=slug,
        cwd=cwd,
        title=_sidecar_title(conv_dir),
        first_prompt=_first_user_prompt(transcript),
        transcript_path=transcript,
        mtime=stat.st_mtime,
        size_bytes=path_size(conv_dir),
        has_subdir=subagents.is_dir(),
        subagent_count=(
            sum(1 for p in subagents.glob("*.jsonl") if p.is_file())
            if subagents.is_dir()
            else 0
        ),
        entrypoint=KIND_AGENT,
    )


def _cli_session(chat_dir: Path, slug: str, cwd: str) -> SessionMeta | None:
    db = chat_dir / "store.db"
    if not db.is_file():
        return None
    try:
        stat = db.stat()
    except OSError:
        return None
    meta = read_cli_meta(db)
    name = meta.get("name")
    return SessionMeta(
        session_id=chat_dir.name,
        project_slug=slug,
        cwd=cwd,
        title=name if isinstance(name, str) and name.strip() else None,
        first_prompt=None,
        transcript_path=db,
        mtime=stat.st_mtime,
        size_bytes=path_size(chat_dir),
        entrypoint=KIND_CLI,
    )


def discover_all(root: Path) -> list[ProjectInfo]:
    """Every Cursor chat, grouped by workspace.

    IDE agent transcripts and CLI chats for the same workspace land in one
    project; CLI buckets whose path we cannot name stay in their own group.
    """
    projects: dict[str, ProjectInfo] = {}
    by_id: dict[tuple[str, str], SessionMeta] = {}

    def bucket_for(slug: str, cwd: str, path: Path) -> ProjectInfo:
        info = projects.get(slug)
        if info is None:
            info = ProjectInfo(slug=slug, cwd_guess=cwd, path=path)
            projects[slug] = info
        return info

    def add(info: ProjectInfo, session: SessionMeta) -> None:
        key = (info.slug, session.session_id)
        existing = by_id.get(key)
        if existing is None:
            by_id[key] = session
            info.sessions.append(session)
            return
        # A CLI chat also writes an agent transcript: one session, two stores.
        existing.size_bytes += session.size_bytes
        existing.mtime = max(existing.mtime, session.mtime)
        existing.title = existing.title or session.title
        existing.first_prompt = existing.first_prompt or session.first_prompt
        existing.entrypoint = KIND_BOTH

    pdir = projects_dir(root)
    if pdir.is_dir():
        for child in sorted(pdir.iterdir()):
            if not child.is_dir() or child.name in NON_PROJECT_DIRS:
                continue
            transcripts = child / TRANSCRIPTS_DIRNAME
            if not transcripts.is_dir():
                continue
            cwd = decode_slug(child.name)
            info = bucket_for(child.name, cwd, child)
            for conv in sorted(transcripts.iterdir()):
                if not conv.is_dir():
                    continue
                session = _agent_session(conv, child.name, cwd)
                if session is not None:
                    add(info, session)

    cdir = chats_dir(root)
    if cdir.is_dir():
        known = bucket_to_cwd(root)
        for bucket in sorted(cdir.iterdir()):
            if not bucket.is_dir():
                continue
            cwd = known.get(bucket.name)
            if cwd:
                slug = slugify_path(cwd)
                info = bucket_for(slug, cwd, projects_dir(root) / slug)
            else:
                slug = f"chats-{bucket.name}"
                info = bucket_for(slug, f"(unknown workspace {bucket.name})", bucket)
                cwd = ""
            for chat in sorted(bucket.iterdir()):
                if not chat.is_dir() or not is_chat_id(chat.name):
                    continue
                session = _cli_session(chat, info.slug, cwd)
                if session is not None:
                    add(info, session)

    ordered = list(projects.values())
    for info in ordered:
        info.sessions.sort(key=lambda s: s.mtime, reverse=True)
    ordered.sort(
        key=lambda p: max((s.mtime for s in p.sessions), default=0), reverse=True
    )
    return [p for p in ordered if p.sessions]


def locate_sessions(root: Path, session_id: str) -> list[Path]:
    """Every directory holding this chat — a chat can exist in both stores."""
    found: list[Path] = []
    pdir = projects_dir(root)
    if pdir.is_dir():
        for child in pdir.iterdir():
            conv = child / TRANSCRIPTS_DIRNAME / session_id
            if conv.is_dir():
                found.append(conv)
    cdir = chats_dir(root)
    if cdir.is_dir():
        for bucket in cdir.iterdir():
            chat = bucket / session_id
            if chat.is_dir():
                found.append(chat)
    return found


def locate_session(root: Path, session_id: str) -> Path | None:
    """First directory holding a chat, agent transcript store first."""
    found = locate_sessions(root, session_id)
    return found[0] if found else None


def session_kind(root: Path, session_id: str) -> str | None:
    directory = locate_session(root, session_id)
    if directory is None:
        return None
    if directory.parent.name == TRANSCRIPTS_DIRNAME:
        return KIND_AGENT
    return KIND_CLI


def cli_bucket_dir(root: Path, cwd: str) -> Path:
    return chats_dir(root) / cwd_bucket(cwd)
