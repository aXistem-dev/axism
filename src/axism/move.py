"""Move sessions (or whole projects) between Claude Code project dirs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta, discover_all
from axism.paths import (
    config_root,
    encode_project_slug,
    is_session_uuid,
    projects_dir,
    tmp_claude_roots,
)

MEMORY_MAX_BYTES = 200 * 1024
MEMORY_MAX_FILES = 80
MEMORY_MAX_COLLISIONS = 25


@dataclass
class MemoryStats:
    file_count: int = 0
    total_bytes: int = 0
    collisions: int = 0
    dual_memory_md: bool = False
    multi_source_with_memory: bool = False

    def over_limits(self) -> bool:
        return (
            self.total_bytes > MEMORY_MAX_BYTES
            or self.file_count > MEMORY_MAX_FILES
            or self.collisions > MEMORY_MAX_COLLISIONS
            or self.multi_source_with_memory
        )

    def summary(self) -> str:
        bits = [
            f"files={self.file_count}",
            f"bytes={self.total_bytes}",
            f"collisions={self.collisions}",
        ]
        if self.dual_memory_md:
            bits.append("dual MEMORY.md")
        if self.multi_source_with_memory:
            bits.append("multi-source+memory")
        return ", ".join(bits)


@dataclass
class MoveResult:
    ok: bool
    message: str
    actions: list[str] = field(default_factory=list)
    dest_cwd: str = ""
    dest_slug: str = ""
    memory_actions: list[str] = field(default_factory=list)
    memory_needs_agent: bool = False
    memory_stats: MemoryStats | None = None
    memory_dest: Path | None = None
    memory_parked: list[Path] = field(default_factory=list)
    memory_renamed: list[Path] = field(default_factory=list)


def normalize_dest_cwd(dest: str) -> str:
    """Normalize a typed destination path to an absolute cwd string."""
    text = dest.strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path("/") / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def resolve_dest(
    dest: str,
    *,
    root: Path | None = None,
    projects: list[ProjectInfo] | None = None,
) -> tuple[str, str, Path] | None:
    """Resolve user dest (cwd path or known slug) → (cwd, slug, projects/<slug>)."""
    root = root or config_root()
    text = dest.strip()
    if not text:
        return None
    projects = projects if projects is not None else discover_all(root)
    by_slug = {p.slug: p for p in projects}
    by_cwd = {p.cwd_guess: p for p in projects}

    if text in by_slug:
        p = by_slug[text]
        return p.cwd_guess, p.slug, p.path

    if text.startswith("-") and "/" not in text:
        return None

    cwd = normalize_dest_cwd(text)
    if not cwd:
        return None
    if cwd in by_cwd:
        p = by_cwd[cwd]
        return p.cwd_guess, p.slug, p.path

    slug = encode_project_slug(cwd)
    if not slug or slug in {".", ".."}:
        return None
    dest_path = projects_dir(root) / slug
    try:
        dest_path.resolve().relative_to(projects_dir(root).resolve())
    except ValueError:
        return None
    return cwd, slug, dest_path


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug_short(slug: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-")
    return (cleaned[-24:] if cleaned else "src") or "src"


def _memory_link_line(filename: str) -> str:
    stem = Path(filename).stem
    title = stem.replace("-", " ").replace("_", " ")
    return f"- [{title}]({filename})"


def _append_memory_links(memory_md: Path, filenames: list[str]) -> int:
    """Append missing bullet links for topic files. Returns lines added."""
    if not filenames:
        return 0
    existing = ""
    if memory_md.is_file():
        existing = memory_md.read_text(encoding="utf-8", errors="replace")
    to_add: list[str] = []
    for name in filenames:
        if name in existing or f"]({name})" in existing:
            continue
        to_add.append(_memory_link_line(name))
    if not to_add:
        return 0
    block = "\n".join(to_add) + "\n"
    if existing and not existing.endswith("\n"):
        block = "\n" + block
    with memory_md.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return len(to_add)


def _iter_memory_files(mem: Path) -> list[Path]:
    if not mem.is_dir():
        return []
    return [p for p in mem.iterdir() if p.is_file() and not p.name.startswith(".")]


def assess_memory_merge(
    sources: list[ProjectInfo],
    dest_path: Path,
) -> MemoryStats:
    """Pre-merge stats for complexity gating."""
    stats = MemoryStats()
    dest_mem = dest_path / "memory"
    dest_files = {p.name: p for p in _iter_memory_files(dest_mem)}
    source_names: set[str] = set()
    sources_with_memory_md = 0

    for src in sources:
        src_mem = src.path / "memory"
        files = _iter_memory_files(src_mem)
        if not files:
            continue
        if (src_mem / "MEMORY.md").is_file():
            sources_with_memory_md += 1
        if (src_mem / "MEMORY.md").is_file() and (dest_mem / "MEMORY.md").is_file():
            stats.dual_memory_md = True
        for p in files:
            source_names.add(p.name)
            stats.file_count += 1
            try:
                stats.total_bytes += p.stat().st_size
            except OSError:
                pass
            if p.name in dest_files and p.name != "MEMORY.md":
                try:
                    if _file_hash(p) != _file_hash(dest_files[p.name]):
                        stats.collisions += 1
                except OSError:
                    stats.collisions += 1

    for p in _iter_memory_files(dest_mem):
        try:
            stats.total_bytes += p.stat().st_size
        except OSError:
            pass
        if p.name not in source_names:
            stats.file_count += 1

    if len(sources) > 1 and sources_with_memory_md >= 1:
        stats.multi_source_with_memory = True
    return stats


def merge_project_memory(
    sources: list[ProjectInfo],
    dest_path: Path,
    *,
    dry_run: bool = False,
) -> tuple[list[str], bool, MemoryStats, list[Path], list[Path]]:
    """Merge source memory/ into dest. Returns actions, needs_agent, stats, parked, renamed."""
    actions: list[str] = []
    parked: list[Path] = []
    renamed: list[Path] = []
    needs_agent = False
    stats = assess_memory_merge(sources, dest_path)
    dest_mem = dest_path / "memory"
    added_topics: list[str] = []

    for src in sources:
        src_mem = src.path / "memory"
        src_files = _iter_memory_files(src_mem)
        if not src_files:
            continue
        short = _slug_short(src.slug)

        if not dest_mem.exists():
            if dry_run:
                actions.append(f"would move memory/ from {src.slug} → {dest_path.name}")
            else:
                try:
                    shutil.move(str(src_mem), str(dest_mem))
                    actions.append(f"moved memory/ from {src.slug}")
                except OSError as exc:
                    actions.append(f"memory move failed ({src.slug}): {exc}")
            continue

        src_md = src_mem / "MEMORY.md"
        dest_md = dest_mem / "MEMORY.md"
        dual = src_md.is_file() and dest_md.is_file()

        for src_file in sorted(src_files, key=lambda p: p.name):
            name = src_file.name
            if name == "MEMORY.md":
                continue
            dest_file = dest_mem / name
            if not dest_file.exists():
                if dry_run:
                    actions.append(f"would move {name} from {src.slug}")
                else:
                    shutil.move(str(src_file), str(dest_file))
                    actions.append(f"moved memory/{name} from {src.slug}")
                    added_topics.append(name)
                continue
            try:
                same = _file_hash(src_file) == _file_hash(dest_file)
            except OSError:
                same = False
            if same:
                if dry_run:
                    actions.append(f"would drop duplicate {name} from {src.slug}")
                else:
                    src_file.unlink(missing_ok=True)
                    actions.append(f"dropped duplicate memory/{name}")
                continue
            stem = Path(name).stem
            suffix = Path(name).suffix
            new_name = f"{stem}-from-{short}{suffix}"
            dest_alt = dest_mem / new_name
            n = 2
            while dest_alt.exists():
                new_name = f"{stem}-from-{short}-{n}{suffix}"
                dest_alt = dest_mem / new_name
                n += 1
            if dry_run:
                actions.append(f"would move {name} → {new_name}")
            else:
                shutil.move(str(src_file), str(dest_alt))
                actions.append(f"moved memory/{name} → {new_name}")
                renamed.append(dest_alt)
                added_topics.append(new_name)

        if src_md.is_file():
            if dual:
                needs_agent = True
                park_name = f"MEMORY-from-{short}.md"
                park_path = dest_mem / park_name
                n = 2
                while park_path.exists():
                    park_name = f"MEMORY-from-{short}-{n}.md"
                    park_path = dest_mem / park_name
                    n += 1
                if dry_run:
                    actions.append(f"would park {src.slug}/MEMORY.md as {park_name}")
                else:
                    shutil.move(str(src_md), str(park_path))
                    actions.append(f"parked MEMORY.md as {park_name}")
                    parked.append(park_path)
            elif not dest_md.is_file():
                if dry_run:
                    actions.append(f"would move MEMORY.md from {src.slug}")
                else:
                    shutil.move(str(src_md), str(dest_md))
                    actions.append(f"moved MEMORY.md from {src.slug}")

        if not dry_run and src_mem.is_dir():
            try:
                if not list(src_mem.iterdir()):
                    src_mem.rmdir()
                    actions.append(f"removed empty memory/ from {src.slug}")
            except OSError as exc:
                actions.append(f"could not remove source memory/: {exc}")

    if (
        not dry_run
        and dest_mem.is_dir()
        and (dest_mem / "MEMORY.md").is_file()
        and added_topics
    ):
        try:
            n = _append_memory_links(dest_mem / "MEMORY.md", added_topics)
            if n:
                actions.append(f"appended {n} link(s) to MEMORY.md")
        except OSError as exc:
            actions.append(f"MEMORY.md links not updated: {exc}")
            needs_agent = True

    if parked:
        needs_agent = True

    return actions, needs_agent, stats, parked, renamed


def _rewrite_cwd_in_jsonl(
    path: Path, new_cwd: str, *, old_cwds: set[str] | None = None
) -> int:
    """Rewrite ``cwd`` fields in a transcript JSONL. Returns lines changed."""
    if not path.is_file():
        return 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    changed = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as src, tmp.open(
            "w", encoding="utf-8"
        ) as dst:
            for line in src:
                out = line
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(line)
                    continue
                if isinstance(obj, dict) and "cwd" in obj:
                    cur = obj.get("cwd")
                    should = old_cwds is None or cur in old_cwds
                    if should and cur != new_cwd:
                        obj["cwd"] = new_cwd
                        out = json.dumps(obj, ensure_ascii=False) + "\n"
                        changed += 1
                dst.write(out)
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return changed


def _rewrite_history_project(
    history_path: Path, session_ids: set[str], new_cwd: str
) -> int:
    """Update ``project`` field for matching sessionIds in history.jsonl."""
    if not history_path.is_file() or not session_ids:
        return 0
    tmp = history_path.with_suffix(".jsonl.tmp")
    changed = 0
    try:
        with history_path.open("r", encoding="utf-8", errors="replace") as src, tmp.open(
            "w", encoding="utf-8"
        ) as dst:
            for line in src:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(line)
                    continue
                if (
                    isinstance(obj, dict)
                    and obj.get("sessionId") in session_ids
                    and obj.get("project") != new_cwd
                ):
                    obj["project"] = new_cwd
                    dst.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    changed += 1
                else:
                    dst.write(line if line.endswith("\n") else line + "\n")
        os.replace(tmp, history_path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return changed


def _update_job_cwd(root: Path, session_id: str, new_cwd: str) -> str | None:
    job_state = root / "jobs" / session_id[:8] / "state.json"
    if not job_state.is_file():
        return None
    try:
        data = json.loads(job_state.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("cwd") != new_cwd:
            data["cwd"] = new_cwd
            job_state.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return f"updated jobs/{session_id[:8]}/state.json cwd"
    except (OSError, json.JSONDecodeError) as exc:
        return f"job state not updated: {exc}"
    return None


def _move_bridge_pointer(src_proj: Path, dest_proj: Path, session_id: str) -> str | None:
    bridge = src_proj / "bridge-pointer.json"
    if not bridge.is_file():
        return None
    try:
        data = json.loads(bridge.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sid = None
    if isinstance(data, dict):
        sid = data.get("sessionId") or data.get("session_id")
    if sid != session_id:
        return None
    dest_bridge = dest_proj / "bridge-pointer.json"
    if dest_bridge.exists():
        return "left bridge-pointer in source (dest already has one)"
    try:
        shutil.move(str(bridge), str(dest_bridge))
        return f"moved bridge-pointer.json → {dest_proj.name}"
    except OSError as exc:
        return f"bridge-pointer not moved: {exc}"


def _move_tmp_scratch(session_id: str, src_slug: str, dest_slug: str) -> list[str]:
    actions: list[str] = []
    for base in tmp_claude_roots():
        src = base / src_slug / session_id
        if not src.exists():
            continue
        dest_parent = base / dest_slug
        dest = dest_parent / session_id
        try:
            dest_parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                actions.append(f"skip tmp {src} (dest exists)")
                continue
            shutil.move(str(src), str(dest))
            actions.append(f"moved tmp scratch {src.name}")
        except OSError as exc:
            actions.append(f"tmp scratch not moved: {exc}")
    return actions


def _ensure_under_projects(path: Path, root: Path) -> None:
    projects = projects_dir(root).resolve()
    path.resolve().relative_to(projects)


def _ensure_workspace_cwd(dest_cwd: str) -> str | None:
    """Create the real workspace directory for a destination cwd if missing.

    Only creates when the parent directory already exists (avoids inventing
    deep trees for typo paths). Returns an action message, or None when the
    directory already exists. Returns a skip message when the parent is missing.
    """
    path = Path(dest_cwd).expanduser()
    if path.exists():
        if path.is_dir():
            return None
        raise NotADirectoryError(f"workspace path exists but is not a directory: {path}")
    parent = path.parent
    if not parent.is_dir():
        return f"skipped workspace create (parent missing: {parent})"
    path.mkdir(exist_ok=True)
    return f"created workspace dir {path}"


def move_sessions(
    sessions: list[SessionMeta],
    dest: str,
    *,
    root: Path | None = None,
    dry_run: bool = False,
) -> MoveResult:
    """Move one or more sessions into a destination project (existing or new path)."""
    root = root or config_root()
    if not sessions:
        return MoveResult(False, "no sessions to move")

    resolved = resolve_dest(dest, root=root)
    if not resolved:
        return MoveResult(False, f"invalid destination: {dest!r}")
    dest_cwd, dest_slug, dest_path = resolved

    to_move: list[SessionMeta] = []
    for s in sessions:
        if s.project_slug == dest_slug:
            continue
        to_move.append(s)
    if not to_move:
        return MoveResult(
            True,
            "already in destination",
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )

    actions: list[str] = []
    if dry_run:
        actions.append(f"would ensure project dir {dest_path}")
        if not Path(dest_cwd).exists():
            actions.append(f"would create workspace dir {dest_cwd}")
        for s in to_move:
            actions.append(
                f"would move {s.session_id[:8]}… {s.project_slug} → {dest_slug}"
            )
        return MoveResult(
            True,
            f"would move {len(to_move)} session(s) → {dest_cwd}",
            actions=actions,
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )

    try:
        _ensure_under_projects(dest_path, root)
        dest_path.mkdir(parents=True, exist_ok=True)
        ws = _ensure_workspace_cwd(dest_cwd)
        if ws:
            actions.append(ws)
    except (OSError, ValueError) as exc:
        return MoveResult(False, f"cannot create destination: {exc}")

    moved_ids: set[str] = set()
    for s in to_move:
        src_proj = projects_dir(root) / s.project_slug
        src_jsonl = src_proj / f"{s.session_id}.jsonl"
        src_dir = src_proj / s.session_id
        dest_jsonl = dest_path / f"{s.session_id}.jsonl"
        dest_dir = dest_path / s.session_id

        if not src_jsonl.is_file():
            actions.append(f"missing transcript {s.session_id[:8]}… — skipped")
            continue
        if dest_jsonl.exists() or dest_dir.exists():
            actions.append(f"collision {s.session_id[:8]}… in dest — skipped")
            continue

        try:
            shutil.move(str(src_jsonl), str(dest_jsonl))
            actions.append(f"moved {s.session_id[:8]}….jsonl → {dest_slug}")
            if src_dir.is_dir():
                shutil.move(str(src_dir), str(dest_dir))
                actions.append(f"moved {s.session_id[:8]}…/ subdir")
            n = _rewrite_cwd_in_jsonl(dest_jsonl, dest_cwd)
            actions.append(f"rewrote cwd in {n} transcript line(s)")
            job_msg = _update_job_cwd(root, s.session_id, dest_cwd)
            if job_msg:
                actions.append(job_msg)
            bridge_msg = _move_bridge_pointer(src_proj, dest_path, s.session_id)
            if bridge_msg:
                actions.append(bridge_msg)
            actions.extend(_move_tmp_scratch(s.session_id, s.project_slug, dest_slug))
            moved_ids.add(s.session_id)
        except OSError as exc:
            actions.append(f"error moving {s.session_id[:8]}…: {exc}")

    if moved_ids:
        hist = root / "history.jsonl"
        try:
            n = _rewrite_history_project(hist, moved_ids, dest_cwd)
            if n:
                actions.append(f"updated {n} history.jsonl project field(s)")
        except OSError as exc:
            actions.append(f"history not updated: {exc}")

    if not moved_ids:
        return MoveResult(
            False,
            "no sessions moved",
            actions=actions,
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )
    return MoveResult(
        True,
        f"moved {len(moved_ids)} session(s) → {dest_cwd}",
        actions=actions,
        dest_cwd=dest_cwd,
        dest_slug=dest_slug,
    )


def move_projects(
    projects: list[ProjectInfo],
    dest: str,
    *,
    root: Path | None = None,
    dry_run: bool = False,
    merge_memory: bool = True,
) -> MoveResult:
    """Move project(s) to a destination path or existing project.

    - One source + new path → rename the project directory and rewrite cwds.
    - Otherwise → move sessions into the destination and merge memory/.
    """
    root = root or config_root()
    if not projects:
        return MoveResult(False, "no projects to move")

    resolved = resolve_dest(dest, root=root)
    if not resolved:
        return MoveResult(False, f"invalid destination: {dest!r}")
    dest_cwd, dest_slug, dest_path = resolved

    sources = [p for p in projects if p.slug != dest_slug]
    if not sources:
        return MoveResult(
            True,
            "already at destination",
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )

    if len(sources) == 1 and not dest_path.exists():
        src = sources[0]
        if dry_run:
            actions_dry = [
                f"would rename {src.path} → {dest_path}",
                f"would rewrite all transcript cwd fields → {dest_cwd!r}",
            ]
            if not Path(dest_cwd).exists():
                actions_dry.append(f"would create workspace dir {dest_cwd}")
            return MoveResult(
                True,
                f"would rename project {src.slug} → {dest_slug}",
                actions=actions_dry,
                dest_cwd=dest_cwd,
                dest_slug=dest_slug,
            )
        actions: list[str] = []
        try:
            _ensure_under_projects(src.path, root)
            _ensure_under_projects(dest_path.parent / dest_slug, root)
            os.replace(src.path, dest_path)
            actions.append(f"renamed project dir → {dest_slug}")
            ws = _ensure_workspace_cwd(dest_cwd)
            if ws:
                actions.append(ws)
        except (OSError, ValueError) as exc:
            return MoveResult(False, f"rename failed: {exc}")

        session_ids: set[str] = set()
        for entry in dest_path.iterdir():
            if (
                entry.is_file()
                and entry.suffix == ".jsonl"
                and is_session_uuid(entry.stem)
            ):
                session_ids.add(entry.stem)
                try:
                    # Rewrite every cwd in the transcript — slug decode is ambiguous
                    # (hyphens vs path separators), so filtering on old_cwd misses most
                    # lines when sessions had nested or hyphenated paths.
                    n = _rewrite_cwd_in_jsonl(entry, dest_cwd)
                    actions.append(f"{entry.stem[:8]}…: rewrote {n} cwd line(s)")
                    job_msg = _update_job_cwd(root, entry.stem, dest_cwd)
                    if job_msg:
                        actions.append(job_msg)
                    actions.extend(_move_tmp_scratch(entry.stem, src.slug, dest_slug))
                except OSError as exc:
                    actions.append(f"{entry.stem[:8]}…: cwd rewrite failed: {exc}")

        try:
            n = _rewrite_history_project(root / "history.jsonl", session_ids, dest_cwd)
            if n:
                actions.append(f"updated {n} history.jsonl project field(s)")
        except OSError as exc:
            actions.append(f"history not updated: {exc}")

        mem = dest_path / "memory"
        return MoveResult(
            True,
            f"moved project → {dest_cwd}",
            actions=actions,
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
            memory_dest=mem if mem.is_dir() else None,
        )

    sessions: list[SessionMeta] = []
    for p in sources:
        sessions.extend(p.sessions)

    if sessions:
        result = move_sessions(sessions, dest_cwd, root=root, dry_run=dry_run)
    else:
        try:
            if not dry_run:
                _ensure_under_projects(dest_path, root)
                dest_path.mkdir(parents=True, exist_ok=True)
                _ensure_workspace_cwd(dest_cwd)
        except (OSError, ValueError) as exc:
            return MoveResult(False, f"cannot create destination: {exc}")
        result = MoveResult(
            True,
            f"no sessions; merging memory → {dest_cwd}",
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )

    if not result.ok and sessions:
        return result

    if merge_memory:
        mem_actions, needs_agent, stats, parked, renamed = merge_project_memory(
            sources, dest_path, dry_run=dry_run
        )
        result.memory_actions = mem_actions
        result.actions.extend(mem_actions)
        result.memory_needs_agent = needs_agent
        result.memory_stats = stats
        result.memory_dest = dest_path / "memory"
        result.memory_parked = parked
        result.memory_renamed = renamed
        if mem_actions and result.ok:
            result.message = f"{result.message}; memory merged"
    return result
