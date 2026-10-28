"""Map a session UUID to all deletable on-disk fragments."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from axism.paths import (
    config_root,
    is_session_uuid,
    path_size,
    short_session_id,
    tmp_claude_roots,
)

# Paths / names that must never appear in a per-session delete plan.
# Shared by every provider — credentials and agent-wide config are off limits.
PROTECTED_NAMES = frozenset(
    {
        ".credentials.json",
        ".env",
        "auth.json",
        "cli-config.json",
        "cli-config.yaml",
        "config.yaml",
        "control.key",
        "credentials.json",
        "mcp-auth.json",
        "mcps",
        "memory",
        "model-catalog",
        "plugins",
        "settings.json",
        "settings.local.json",
        "skills",
        "opencode.db",
        "opencode.db-shm",
        "opencode.db-wal",
        "state.db",
    }
)


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def is_safe_id(value: str | None) -> bool:
    """True for a plain id / slug usable as a single path component."""
    return isinstance(value, str) and bool(SAFE_ID_RE.fullmatch(value))


def _slug_ok(slug: str) -> bool:
    # Project slugs start with '-' (encoded leading slash) and contain no separators.
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", slug)) and slug not in {".", ".."}


@dataclass
class Fragment:
    kind: str
    path: Path
    size_bytes: int
    optional: bool = False

    def is_protected(self) -> bool:
        parts = set(self.path.parts)
        if parts & PROTECTED_NAMES:
            return True
        if self.path.name in PROTECTED_NAMES:
            return True
        # Never delete project memory directories
        if "memory" in self.path.parts and self.path.name == "memory":
            return True
        return False


@dataclass
class FragmentInventory:
    session_id: str
    project_slug: str | None
    fragments: list[Fragment] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.fragments if not f.is_protected())

    @property
    def deletable(self) -> list[Fragment]:
        return [f for f in self.fragments if not f.is_protected()]

    def stored_rows_note(self) -> str | None:
        """For database-backed agents: where the session's size sits.

        The session's rows are removed through the agent; the protected
        database file itself is never a removal target.
        """
        rows = next(
            (f for f in self.fragments if f.kind == "session_rows" and f.is_protected()),
            None,
        )
        if rows is None:
            return None
        return (
            f"stored in the agent's database ({rows.size_bytes} B; "
            "removed through the agent, the database file stays)"
        )


def _add_if_exists(
    out: list[Fragment], kind: str, path: Path, *, optional: bool = False
) -> None:
    try:
        if os.path.lexists(path):
            # Keep the final component unresolved so a symlink is removed as
            # the link, never through to its target.
            link = path.is_symlink()
            out.append(
                Fragment(
                    kind=kind,
                    path=path.parent.resolve() / path.name,
                    size_bytes=path.lstat().st_size if link else path_size(path),
                    optional=optional,
                )
            )
    except OSError:
        pass


def collect_fragments(
    session_id: str,
    *,
    project_slug: str | None = None,
    root: Path | None = None,
) -> FragmentInventory:
    """Collect all known on-disk fragments for a session UUID."""
    root = root or config_root()
    inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
    if not is_safe_id(session_id) or (
        project_slug is not None and not _slug_ok(project_slug)
    ):
        return inv
    frags = inv.fragments
    short = short_session_id(session_id)
    projects = root / "projects"

    # Transcript + session subdir (all projects or specific slug)
    if projects.is_dir():
        slugs = [project_slug] if project_slug else [
            p.name for p in projects.iterdir() if p.is_dir()
        ]
        for slug in slugs:
            pdir = projects / slug
            _add_if_exists(frags, "transcript", pdir / f"{session_id}.jsonl")
            _add_if_exists(frags, "session_dir", pdir / session_id)
            # bridge pointer only if it points at this session — checked later in delete
            bridge = pdir / "bridge-pointer.json"
            if bridge.is_file():
                try:
                    data = json.loads(bridge.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("sessionId") == session_id:
                        _add_if_exists(frags, "bridge_pointer", bridge)
                except (OSError, json.JSONDecodeError):
                    pass

    _add_if_exists(frags, "file_history", root / "file-history" / session_id)
    _add_if_exists(frags, "uploads", root / "uploads" / session_id)
    _add_if_exists(frags, "session_env", root / "session-env" / session_id)
    _add_if_exists(frags, "tasks", root / "tasks" / session_id)
    _add_if_exists(frags, "jobs", root / "jobs" / short)

    # Live session registry files keyed by PID — match by sessionId content
    sessions_dir = root / "sessions"
    if sessions_dir.is_dir():
        for entry in sessions_dir.iterdir():
            if not entry.name.endswith(".json"):
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("sessionId") == session_id:
                _add_if_exists(frags, "live_registry", entry, optional=True)
                # companion .key files: <pid>.<hash>.key
                pid = data.get("pid")
                if pid is not None:
                    for keyf in sessions_dir.glob(f"{pid}.*.key"):
                        _add_if_exists(frags, "live_key", keyf, optional=True)

    # Ephemeral tmp scratch
    for tmp_root in tmp_claude_roots():
        if not tmp_root.is_dir():
            continue
        if project_slug:
            _add_if_exists(
                frags,
                "tmp_scratch",
                tmp_root / project_slug / session_id,
                optional=True,
            )
        else:
            for slug_dir in tmp_root.iterdir():
                if slug_dir.is_dir():
                    _add_if_exists(
                        frags,
                        "tmp_scratch",
                        slug_dir / session_id,
                        optional=True,
                    )

    # history.jsonl is handled specially in delete.py (line filter), not as a path wipe
    history = root / "history.jsonl"
    if history.is_file():
        # Count matching lines / estimate size contribution
        matched = 0
        matched_bytes = 0
        try:
            with history.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if session_id in line:
                        matched += 1
                        matched_bytes += len(line.encode("utf-8"))
        except OSError:
            pass
        if matched:
            frags.append(
                Fragment(
                    kind="history_lines",
                    path=history,
                    size_bytes=matched_bytes,
                    optional=False,
                )
            )

    # Deduplicate by path
    seen: set[Path] = set()
    unique: list[Fragment] = []
    for f in frags:
        if f.path in seen:
            continue
        seen.add(f.path)
        unique.append(f)
    inv.fragments = unique
    return inv


def find_orphan_job_dirs(root: Path | None = None) -> list[Fragment]:
    """Job dirs whose short id has no matching transcript (best-effort)."""
    root = root or config_root()
    jobs = root / "jobs"
    projects = root / "projects"
    if not jobs.is_dir():
        return []
    known_shorts: set[str] = set()
    if projects.is_dir():
        for p in projects.rglob("*.jsonl"):
            if is_session_uuid(p.stem):
                known_shorts.add(short_session_id(p.stem))
    orphans: list[Fragment] = []
    for d in jobs.iterdir():
        if d.is_dir() and len(d.name) == 8 and d.name not in known_shorts:
            orphans.append(
                Fragment(kind="orphan_job", path=d, size_bytes=path_size(d), optional=True)
            )
    return orphans
