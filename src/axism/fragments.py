"""Map a session UUID to all deletable on-disk fragments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from axism.paths import (
    config_root,
    is_session_uuid,
    path_size,
    short_session_id,
    tmp_claude_roots,
)

# Paths / names that must never appear in a per-session delete plan
PROTECTED_NAMES = frozenset(
    {
        ".credentials.json",
        "settings.json",
        "settings.local.json",
        "skills",
        "plugins",
        "memory",
        "control.key",
        "model-catalog",
    }
)


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


def _add_if_exists(
    out: list[Fragment], kind: str, path: Path, *, optional: bool = False
) -> None:
    try:
        if path.exists():
            out.append(
                Fragment(
                    kind=kind,
                    path=path.resolve(),
                    size_bytes=path_size(path),
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
    if not is_session_uuid(session_id):
        # still allow short prefix lookup via caller; require full uuid here
        pass
    root = root or config_root()
    inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
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
                    import json

                    data = json.loads(bridge.read_text(encoding="utf-8"))
                    if data.get("sessionId") == session_id:
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
        import json

        for entry in sessions_dir.iterdir():
            if not entry.name.endswith(".json"):
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
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
