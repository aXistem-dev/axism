"""Copy/convert sessions between agent tools (lossy text-turn fidelity).

Same-provider relocate stays in each backend's ``move_sessions``. This module
builds a canonical turn list from a source session and writes a new session
into the destination backend under a chosen cwd.
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from axism.discover import SessionMeta
from axism.move import MoveResult, normalize_dest_cwd
from axism.paths import encode_project_slug
from axism.paths import projects_dir as claude_projects_dir
from axism.providers.base import SessionProvider
from axism.providers.cursor_store.paths import (
    TITLE_SIDECAR,
    TRANSCRIPTS_DIRNAME,
    slugify_path,
)
from axism.providers.cursor_store.paths import (
    projects_dir as cursor_projects_dir,
)
from axism.providers.hermes_store import ops as hermes_ops

USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE
)


@dataclass
class Turn:
    role: str  # user | assistant
    content: str


@dataclass
class CanonicalTranscript:
    title: str
    cwd: str | None
    source_provider: str
    source_session_id: str
    turns: list[Turn] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.turns)} turns from {self.source_provider}:"
            f"{self.source_session_id[:12]}"
        )


@dataclass
class TransferResult:
    ok: bool
    message: str
    actions: list[str] = field(default_factory=list)
    new_session_id: str = ""
    dest_provider: str = ""
    dest_cwd: str = ""

    def as_move_result(self) -> MoveResult:
        return MoveResult(
            ok=self.ok,
            message=self.message,
            actions=list(self.actions),
            dest_cwd=self.dest_cwd,
        )


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name") or "tool"
                    parts.append(f"[ran tool: {name}]")
                elif block.get("type") == "tool_result":
                    parts.append("[tool result]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p.strip() for p in parts if p and str(p).strip()).strip()
    return ""


def _strip_user_query(text: str) -> str:
    match = USER_QUERY_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _append_turn(turns: list[Turn], role: str, content: str) -> None:
    clean = content.strip()
    if not clean:
        return
    if role not in {"user", "assistant"}:
        return
    if turns and turns[-1].role == role:
        turns[-1].content = f"{turns[-1].content}\n{clean}"
    else:
        turns.append(Turn(role=role, content=clean))


def export_claude_jsonl(path: Path, *, session: SessionMeta) -> CanonicalTranscript:
    turns: list[Turn] = []
    title = session.display_title
    cwd = session.cwd
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return CanonicalTranscript(
            title=title,
            cwd=cwd,
            source_provider="claude_code",
            source_session_id=session.session_id,
            turns=[],
        )
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
        typ = obj.get("type")
        if typ == "custom-title":
            t = obj.get("title") or (obj.get("message") or {}).get("content")
            if isinstance(t, str) and t.strip():
                title = t.strip()
            continue
        if typ in {"user", "assistant"}:
            msg = obj.get("message")
            body = _extract_text(msg.get("content") if isinstance(msg, dict) else msg)
            if typ == "user":
                body = _strip_user_query(body)
            _append_turn(turns, typ, body)
            continue
        # Cursor-shaped rows sometimes land in Claude trees via import.
        role = obj.get("role")
        if role in {"user", "assistant"}:
            msg = obj.get("message")
            body = _extract_text(
                msg.get("content") if isinstance(msg, dict) else obj.get("content")
            )
            if role == "user":
                body = _strip_user_query(body)
            _append_turn(turns, role, body)
    return CanonicalTranscript(
        title=title or session.session_id,
        cwd=cwd,
        source_provider=session.provider or "claude_code",
        source_session_id=session.session_id,
        turns=turns,
    )


def export_cursor_jsonl(path: Path, *, session: SessionMeta) -> CanonicalTranscript:
    return export_claude_jsonl(path, session=session)


def export_hermes_session(
    provider: SessionProvider, session: SessionMeta
) -> CanonicalTranscript:
    """Prefer ``hermes sessions export --format trace``; fall back to empty turns."""
    root = provider.config_root()
    with tempfile.TemporaryDirectory(prefix="axism-hermes-export-") as tmp:
        out = Path(tmp) / "trace.jsonl"
        rc, msg = hermes_ops.run_hermes(
            root,
            [
                "sessions",
                "export",
                "--format",
                "trace",
                "--session-id",
                session.session_id,
                "--no-redact",
                str(out),
            ],
            timeout=120.0,
        )
        if rc == 0 and out.is_file():
            canonical = export_claude_jsonl(out, session=session)
            canonical.source_provider = "hermes"
            if not canonical.title or canonical.title == session.session_id:
                canonical.title = session.display_title
            return canonical
        # Soft failure: still return metadata so import can seed a stub.
        return CanonicalTranscript(
            title=session.display_title,
            cwd=session.cwd,
            source_provider="hermes",
            source_session_id=session.session_id,
            turns=[
                Turn(
                    role="user",
                    content=(
                        f"(Hermes export failed: {msg or 'unknown error'}. "
                        f"Stub session for {session.session_id}.)"
                    ),
                )
            ],
        )


def export_session(
    provider: SessionProvider, session: SessionMeta
) -> CanonicalTranscript:
    name = provider.name
    if name == "hermes":
        return export_hermes_session(provider, session)
    path = session.transcript_path
    if path.is_file():
        if name == "cursor":
            return export_cursor_jsonl(path, session=session)
        return export_claude_jsonl(path, session=session)
    # Cursor CLI light chats: try prompt_history next to meta
    parent = path if path.is_dir() else path.parent
    history = parent / "prompt_history.json"
    turns: list[Turn] = []
    if history.is_file():
        try:
            data = json.loads(history.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and item.strip():
                        _append_turn(turns, "user", item)
                    elif isinstance(item, dict):
                        text = item.get("prompt") or item.get("text") or item.get("content")
                        if isinstance(text, str) and text.strip():
                            _append_turn(turns, "user", text)
        except (OSError, json.JSONDecodeError):
            pass
    return CanonicalTranscript(
        title=session.display_title,
        cwd=session.cwd,
        source_provider=session.provider or name,
        source_session_id=session.session_id,
        turns=turns,
    )


def _write_claude_jsonl(
    dest_root: Path, dest_cwd: str, canonical: CanonicalTranscript
) -> tuple[str, Path]:
    slug = encode_project_slug(dest_cwd)
    new_id = str(uuid.uuid4())
    project = claude_projects_dir(dest_root) / slug
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{new_id}.jsonl"
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    parent: str | None = None
    lines: list[str] = []

    def emit(obj: dict) -> None:
        nonlocal parent
        uid = str(uuid.uuid4())
        obj.setdefault("uuid", uid)
        obj.setdefault("parentUuid", parent)
        obj.setdefault("sessionId", new_id)
        obj.setdefault("cwd", dest_cwd)
        obj.setdefault("timestamp", now)
        lines.append(json.dumps(obj, ensure_ascii=False))
        parent = uid

    emit(
        {
            "type": "custom-title",
            "customTitle": canonical.title[:200],
        }
    )
    for turn in canonical.turns:
        emit(
            {
                "type": turn.role,
                "message": {
                    "role": turn.role,
                    "content": [{"type": "text", "text": turn.content}],
                },
            }
        )
    if len(canonical.turns) == 0:
        emit(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"(Imported empty session from {canonical.source_provider})",
                        }
                    ],
                },
            }
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new_id, path


def _write_cursor_agent_jsonl(
    dest_root: Path, dest_cwd: str, canonical: CanonicalTranscript
) -> tuple[str, Path]:
    new_id = str(uuid.uuid4())
    base = (
        cursor_projects_dir(dest_root)
        / slugify_path(dest_cwd)
        / TRANSCRIPTS_DIRNAME
        / new_id
    )
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{new_id}.jsonl"
    lines: list[str] = []
    for turn in canonical.turns:
        text = turn.content
        if turn.role == "user":
            text = f"<user_query>\n{text}\n</user_query>"
        lines.append(
            json.dumps(
                {
                    "role": turn.role,
                    "message": {"content": [{"type": "text", "text": text}]},
                },
                ensure_ascii=False,
            )
        )
    if not lines:
        lines.append(
            json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"<user_query>\n(Imported from "
                                    f"{canonical.source_provider})\n</user_query>"
                                ),
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = base / TITLE_SIDECAR
    sidecar.write_text(
        json.dumps({"title": canonical.title[:200]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return new_id, path


def _parse_hermes_import_id(output: str) -> str:
    """Pull the new Hermes session id out of ``hermes sessions import`` output."""
    # e.g. "Imported Claude Code session as 20260923_114611_63d9b7"
    match = re.search(
        r"(?:session as|resume)\s+(\d{8}_\d{6}_[0-9a-f]+)",
        output,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{8}_\d{6}_[0-9a-f]+)\b", output)
    return match.group(1) if match else ""


def _write_temp_claude_for_hermes(
    canonical: CanonicalTranscript, dest_cwd: str
) -> Path:
    """Write a Claude-shaped JSONL for ``hermes sessions import``.

    ``dest_cwd`` is forced into every record so Hermes groups the session under
    the destination workspace the user picked (not the source transcript cwd).
    """
    tmp = Path(tempfile.mkdtemp(prefix="axism-import-"))
    fake_root = tmp / "claude-home"
    fake_root.mkdir()
    cwd = normalize_dest_cwd(dest_cwd) or dest_cwd or canonical.cwd or str(tmp / "workspace")
    # Stamp dest into the canonical so titles/metadata stay coherent.
    stamped = CanonicalTranscript(
        title=canonical.title,
        cwd=cwd,
        source_provider=canonical.source_provider,
        source_session_id=canonical.source_session_id,
        turns=list(canonical.turns),
    )
    new_id, written = _write_claude_jsonl(fake_root, cwd, stamped)
    flat = tmp / f"{new_id}.jsonl"
    flat.write_text(written.read_text(encoding="utf-8"), encoding="utf-8")
    return flat


def import_to_provider(
    dest: SessionProvider,
    canonical: CanonicalTranscript,
    dest_cwd: str,
    *,
    dry_run: bool = False,
) -> TransferResult:
    dest_cwd = normalize_dest_cwd(dest_cwd) or dest_cwd
    actions: list[str] = [
        f"export {canonical.summary()}",
        f"import → {dest.name} @ {dest_cwd}",
    ]
    if dry_run:
        return TransferResult(
            ok=True,
            message=f"dry-run: would copy to {dest.label} under {dest_cwd}",
            actions=actions + ["dry-run"],
            dest_provider=dest.name,
            dest_cwd=dest_cwd,
        )

    try:
        if dest.name == "claude_code":
            new_id, path = _write_claude_jsonl(dest.config_root(), dest_cwd, canonical)
            actions.append(f"wrote {path}")
            return TransferResult(
                ok=True,
                message=f"Copied to Claude Code as {new_id[:8]}… (text turns only)",
                actions=actions,
                new_session_id=new_id,
                dest_provider=dest.name,
                dest_cwd=dest_cwd,
            )
        if dest.name == "cursor":
            new_id, path = _write_cursor_agent_jsonl(
                dest.config_root(), dest_cwd, canonical
            )
            actions.append(f"wrote {path}")
            return TransferResult(
                ok=True,
                message=(
                    f"Copied to Cursor agent transcript {new_id[:8]}… "
                    "(resume may be incomplete)"
                ),
                actions=actions,
                new_session_id=new_id,
                dest_provider=dest.name,
                dest_cwd=dest_cwd,
            )
        if dest.name == "hermes":
            # Prefer native Claude file import when source path is Claude JSONL.
            src_path = _write_temp_claude_for_hermes(canonical, dest_cwd)
            tmp_root = src_path.parent
            try:
                rc, out = hermes_ops.run_hermes(
                    dest.config_root(),
                    ["sessions", "import", "--from", "claude", str(src_path)],
                    timeout=120.0,
                )
            finally:
                import shutil

                shutil.rmtree(tmp_root, ignore_errors=True)
            if rc != 0:
                return TransferResult(
                    ok=False,
                    message=out or "hermes sessions import failed",
                    actions=actions,
                    dest_provider=dest.name,
                    dest_cwd=dest_cwd,
                )
            actions.append(out or "hermes import ok")
            actions.append(f"workspace cwd={dest_cwd}")
            new_id = _parse_hermes_import_id(out or "")
            if new_id and canonical.title.strip():
                ok, msg = hermes_ops.rename_session(
                    dest.config_root(), new_id, canonical.title.strip()[:200]
                )
                actions.append(msg if ok else f"title note: {msg}")
            return TransferResult(
                ok=True,
                message=(
                    f"Imported into Hermes under {dest_cwd}"
                    + (f" as {new_id}" if new_id else "")
                    + f" ({(out or 'ok').splitlines()[0]}; text turns / tools collapsed)"
                ),
                actions=actions,
                new_session_id=new_id or "",
                dest_provider=dest.name,
                dest_cwd=dest_cwd,
            )
    except OSError as exc:
        return TransferResult(
            ok=False,
            message=f"transfer failed: {exc}",
            actions=actions,
            dest_provider=dest.name,
            dest_cwd=dest_cwd,
        )

    return TransferResult(
        ok=False,
        message=f"{dest.label} cannot import sessions yet",
        actions=actions,
        dest_provider=dest.name,
        dest_cwd=dest_cwd,
    )


def transfer_session(
    source: SessionProvider,
    dest: SessionProvider,
    session: SessionMeta,
    dest_cwd: str,
    *,
    dry_run: bool = False,
    title: str | None = None,
    allow_same_provider: bool = False,
) -> TransferResult:
    """Copy ``session`` from ``source`` into ``dest`` under ``dest_cwd``.

    Set ``allow_same_provider=True`` for same-agent duplicates (Copy).
    Optional ``title`` overrides the destination session title.
    """
    if source.name == dest.name and not allow_same_provider:
        return TransferResult(
            ok=False,
            message="Source and destination agent are the same — use Move or Copy",
            dest_provider=dest.name,
            dest_cwd=dest_cwd,
        )
    canonical = export_session(source, session)
    if title and title.strip():
        canonical.title = title.strip()
    elif allow_same_provider and source.name == dest.name:
        # Same-agent duplicate: make the copy obvious if title unchanged.
        base = canonical.title.strip() or session.display_title
        if not base.endswith(" (copy)"):
            canonical.title = f"{base} (copy)"
    if not canonical.turns and source.name != "hermes":
        canonical.turns = [
            Turn(
                role="user",
                content=f"(No transcript turns found for {session.session_id})",
            )
        ]
    result = import_to_provider(dest, canonical, dest_cwd, dry_run=dry_run)
    if (
        result.ok
        and not dry_run
        and result.new_session_id
        and title
        and title.strip()
        and dest.capabilities.rename
    ):
        # Reinforce title for backends that store it outside the transcript seed.
        ok, msg = dest.rename(result.new_session_id, title.strip())
        result.actions.append(msg if ok else f"title note: {msg}")
    return result


def transfer_sessions(
    source: SessionProvider,
    dest: SessionProvider,
    sessions: list[SessionMeta],
    dest_cwd: str,
    *,
    dry_run: bool = False,
    title: str | None = None,
    allow_same_provider: bool = False,
) -> TransferResult:
    if not sessions:
        return TransferResult(ok=False, message="No sessions to transfer")
    actions: list[str] = []
    ids: list[str] = []
    for i, session in enumerate(sessions):
        # Only apply an explicit title to a single-session copy.
        sess_title = title if len(sessions) == 1 else None
        result = transfer_session(
            source,
            dest,
            session,
            dest_cwd,
            dry_run=dry_run,
            title=sess_title,
            allow_same_provider=allow_same_provider,
        )
        actions.extend(result.actions)
        if not result.ok:
            return TransferResult(
                ok=False,
                message=result.message,
                actions=actions,
                dest_provider=dest.name,
                dest_cwd=normalize_dest_cwd(dest_cwd) or dest_cwd,
            )
        if result.new_session_id:
            ids.append(result.new_session_id)
    n = len(sessions)
    verb = "Copied"
    return TransferResult(
        ok=True,
        message=f"{verb} {n} session{'s' if n != 1 else ''} to {dest.label}",
        actions=actions,
        new_session_id=ids[0] if len(ids) == 1 else "",
        dest_provider=dest.name,
        dest_cwd=normalize_dest_cwd(dest_cwd) or dest_cwd,
    )


def copy_sessions(
    source: SessionProvider,
    dest: SessionProvider,
    sessions: list[SessionMeta],
    dest_cwd: str,
    *,
    dry_run: bool = False,
    title: str | None = None,
) -> TransferResult:
    """Duplicate sessions into ``dest`` (same or other agent); source kept."""
    return transfer_sessions(
        source,
        dest,
        sessions,
        dest_cwd,
        dry_run=dry_run,
        title=title,
        allow_same_provider=True,
    )
