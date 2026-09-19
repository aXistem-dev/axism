"""Safe per-session deletion with dry-run plans."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from axism.fragments import FragmentInventory, collect_fragments
from axism.live import LiveSession, kill_live_session, merge_live
from axism.paths import config_root, is_session_uuid


@dataclass
class DeletePlan:
    session_id: str
    inventory: FragmentInventory
    blocked_reason: str | None = None
    live: LiveSession | None = None
    actions: list[str] = field(default_factory=list)

    @property
    def can_delete(self) -> bool:
        return self.blocked_reason is None

    def summary_lines(self) -> list[str]:
        lines = [
            f"Session: {self.session_id}",
            f"Fragments: {len(self.inventory.deletable)}",
            f"Bytes: {self.inventory.total_bytes}",
        ]
        if self.blocked_reason:
            lines.append(f"BLOCKED: {self.blocked_reason}")
        if self.live:
            lines.append(
                f"Live: kind={self.live.kind} state={self.live.state} "
                f"name={self.live.name!r}"
            )
        for f in self.inventory.deletable:
            lines.append(f"  [{f.kind}] {f.path} ({f.size_bytes} B)")
        return lines


def build_delete_plan(
    session_id: str,
    *,
    project_slug: str | None = None,
    root: Path | None = None,
    force: bool = False,
    use_cli: bool = True,
) -> DeletePlan:
    root = root or config_root()
    if not is_session_uuid(session_id) and len(session_id) < 8:
        inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
        return DeletePlan(
            session_id=session_id,
            inventory=inv,
            blocked_reason="session id too short / invalid",
        )

    # Resolve full UUID if short prefix was given
    full_id = session_id
    if not is_session_uuid(session_id):
        from axism.discover import find_session

        found = find_session(session_id, root)
        if not found:
            inv = collect_fragments(session_id, project_slug=project_slug, root=root)
            return DeletePlan(
                session_id=session_id,
                inventory=inv,
                blocked_reason="session not found",
            )
        _proj, sess = found
        full_id = sess.session_id
        project_slug = project_slug or sess.project_slug

    inv = collect_fragments(full_id, project_slug=project_slug, root=root)
    live_map = merge_live(root, use_cli=use_cli)
    live = live_map.get(full_id)
    plan = DeletePlan(session_id=full_id, inventory=inv, live=live)

    if live and live.is_live and not force:
        if live.kind == "interactive":
            plan.blocked_reason = (
                "interactive session appears live "
                f"(pid={live.pid}, state={live.state}); stop it or pass --force"
            )
        else:
            plan.blocked_reason = (
                "background session appears active "
                f"(state={live.state}); stop it first or pass --force "
                "(will try claude stop/rm)"
            )
    return plan


def _rewrite_history(history_path: Path, session_id: str) -> int:
    """Remove history.jsonl lines for session_id. Returns removed line count."""
    if not history_path.is_file():
        return 0
    tmp = history_path.with_suffix(".jsonl.tmp")
    removed = 0
    try:
        with history_path.open("r", encoding="utf-8", errors="replace") as src, tmp.open(
            "w", encoding="utf-8"
        ) as dst:
            for line in src:
                keep = True
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("sessionId") == session_id:
                        keep = False
                except json.JSONDecodeError:
                    if session_id in line:
                        keep = False
                if keep:
                    dst.write(line)
                else:
                    removed += 1
        os.replace(tmp, history_path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return removed


def _remove_path(path: Path) -> str:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return f"removed file {path}"
    if path.is_dir():
        shutil.rmtree(path)
        return f"removed dir {path}"
    return f"missing {path}"


def _stop_live_if_needed(
    plan: DeletePlan,
    *,
    root: Path,
    dry_run: bool,
    force: bool,
    stop_live: bool,
) -> list[str]:
    """Stop background/CLI job and/or signal PID before deleting files.

    Returns action lines. If stop fails and ``force`` is False, the last line
    starts with ``aborted`` and the caller should not continue deleting.
    """
    if not stop_live or not plan.live:
        return []
    live = plan.live
    if not live.is_killable:
        return []

    label = (
        f"kind={live.kind} state={live.state} pid={live.pid}"
        if live.pid is not None
        else f"kind={live.kind} state={live.state}"
    )
    if dry_run:
        return [f"would stop/kill live session ({label})"]

    ok, msg = kill_live_session(plan.session_id, live, root=root, use_cli=True)
    lines = [f"stop/kill: {msg}"]
    if not ok and not force:
        lines.append("aborted: could not stop live session")
    return lines


def execute_delete(
    plan: DeletePlan,
    *,
    root: Path | None = None,
    dry_run: bool = True,
    force: bool = False,
    stop_live: bool = True,
) -> list[str]:
    """Execute or dry-run a delete plan. Returns action log lines."""
    root = root or config_root()
    actions: list[str] = []

    if plan.blocked_reason and not force:
        actions.append(f"aborted: {plan.blocked_reason}")
        return actions

    stop_lines = _stop_live_if_needed(
        plan, root=root, dry_run=dry_run, force=force, stop_live=stop_live
    )
    actions.extend(stop_lines)
    if any(a.startswith("aborted") for a in stop_lines):
        plan.actions = actions
        return actions

    for frag in plan.inventory.deletable:
        if frag.kind == "history_lines":
            if dry_run:
                actions.append(
                    f"would rewrite {frag.path} (remove lines for {plan.session_id})"
                )
            else:
                n = _rewrite_history(frag.path, plan.session_id)
                actions.append(f"rewrote history.jsonl, removed {n} lines")
            continue

        # Skip live registry if process still alive unless force
        if frag.kind in {"live_registry", "live_key"} and not force:
            if plan.live and plan.live.kind == "interactive" and plan.live.is_live:
                actions.append(f"skip live file {frag.path}")
                continue

        if dry_run:
            actions.append(f"would remove [{frag.kind}] {frag.path} ({frag.size_bytes} B)")
        else:
            try:
                actions.append(_remove_path(frag.path))
            except OSError as exc:
                actions.append(f"error removing {frag.path}: {exc}")

    plan.actions = actions
    return actions


def execute_deletes(
    plans: list[DeletePlan],
    *,
    root: Path | None = None,
    dry_run: bool = True,
    force: bool = False,
    stop_live: bool = True,
) -> list[str]:
    """Execute multiple session delete plans; rewrite history once for all IDs."""
    root = root or config_root()
    actions: list[str] = []
    if not plans:
        return ["nothing to delete"]

    blocked = [p for p in plans if p.blocked_reason and not force]
    if blocked and not force:
        for p in blocked:
            actions.append(f"aborted {p.session_id}: {p.blocked_reason}")
        return actions

    history_ids: list[str] = []
    history_path: Path | None = None

    for plan in plans:
        stop_lines = _stop_live_if_needed(
            plan, root=root, dry_run=dry_run, force=force, stop_live=stop_live
        )
        for line in stop_lines:
            # Prefix multi-delete stop lines with short id for clarity
            if line.startswith(("would ", "stop/kill")):
                actions.append(f"{plan.session_id[:8]}: {line}")
            else:
                actions.append(f"{plan.session_id[:8]}: {line}")
        if any(a.startswith("aborted") for a in stop_lines):
            continue

        for frag in plan.inventory.deletable:
            if frag.kind == "history_lines":
                history_ids.append(plan.session_id)
                history_path = frag.path
                continue
            if frag.kind in {"live_registry", "live_key"} and not force:
                if plan.live and plan.live.kind == "interactive" and plan.live.is_live:
                    actions.append(f"skip live file {frag.path}")
                    continue
            if dry_run:
                actions.append(
                    f"would remove [{frag.kind}] {frag.path} ({frag.size_bytes} B)"
                )
            else:
                try:
                    actions.append(_remove_path(frag.path))
                except OSError as exc:
                    actions.append(f"error removing {frag.path}: {exc}")

    if history_ids and history_path is not None:
        uniq = list(dict.fromkeys(history_ids))
        if dry_run:
            actions.append(
                f"would rewrite {history_path} (remove lines for {len(uniq)} sessions)"
            )
        else:
            n = _rewrite_history_many(history_path, uniq)
            actions.append(f"rewrote history.jsonl, removed {n} lines")

    return actions


def _rewrite_history_many(history_path: Path, session_ids: list[str]) -> int:
    if not history_path.is_file() or not session_ids:
        return 0
    id_set = set(session_ids)
    tmp = history_path.with_suffix(".jsonl.tmp")
    removed = 0
    try:
        with history_path.open("r", encoding="utf-8", errors="replace") as src, tmp.open(
            "w", encoding="utf-8"
        ) as dst:
            for line in src:
                keep = True
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("sessionId") in id_set:
                        keep = False
                except json.JSONDecodeError:
                    if any(sid in line for sid in id_set):
                        keep = False
                if keep:
                    dst.write(line)
                else:
                    removed += 1
        os.replace(tmp, history_path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return removed


@dataclass
class ProjectDeletePlan:
    slug: str
    project_path: Path
    session_plans: list[DeletePlan] = field(default_factory=list)
    extra_paths: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    blocked_reason: str | None = None
    keep_memory: bool = False

    @property
    def can_delete(self) -> bool:
        return self.blocked_reason is None

    def summary_lines(self) -> list[str]:
        lines = [
            f"Project: {self.slug}",
            f"Path: {self.project_path}",
            f"Sessions: {len(self.session_plans)}",
            f"Bytes (approx): {self.total_bytes}",
            f"Keep memory: {self.keep_memory}",
        ]
        if self.blocked_reason:
            lines.append(f"BLOCKED: {self.blocked_reason}")
        for sp in self.session_plans:
            lines.append(f"  session {sp.session_id} fragments={len(sp.inventory.deletable)}")
            if sp.blocked_reason:
                lines.append(f"    BLOCKED: {sp.blocked_reason}")
        for p in self.extra_paths:
            lines.append(f"  extra: {p}")
        return lines


def build_project_delete_plan(
    slug_or_path: str,
    *,
    root: Path | None = None,
    force: bool = False,
    use_cli: bool = True,
    keep_memory: bool = False,
) -> ProjectDeletePlan:
    """Plan deletion of an entire projects/<slug> directory and session fragments."""
    from axism.discover import discover_project
    from axism.paths import encode_project_slug, path_size, projects_dir

    root = root or config_root()
    pdir = projects_dir(root)

    slug = slug_or_path
    project_path = pdir / slug
    if not project_path.is_dir():
        # Treat as cwd
        candidate = Path(slug_or_path).expanduser()
        encoded = encode_project_slug(str(candidate))
        if (pdir / encoded).is_dir():
            slug = encoded
            project_path = pdir / slug
        elif candidate.is_dir() and candidate.parent.resolve() == pdir.resolve():
            slug = candidate.name
            project_path = candidate
        else:
            return ProjectDeletePlan(
                slug=slug_or_path,
                project_path=project_path,
                blocked_reason=f"project not found: {slug_or_path}",
                keep_memory=keep_memory,
            )

    # Safety: only under projects/
    try:
        project_path.resolve().relative_to(pdir.resolve())
    except ValueError:
        return ProjectDeletePlan(
            slug=slug,
            project_path=project_path,
            blocked_reason="refusing to delete path outside projects/",
            keep_memory=keep_memory,
        )

    info = discover_project(project_path)
    session_plans = [
        build_delete_plan(
            s.session_id,
            project_slug=slug,
            root=root,
            force=force,
            use_cli=use_cli,
        )
        for s in info.sessions
    ]

    plan = ProjectDeletePlan(
        slug=slug,
        project_path=project_path,
        session_plans=session_plans,
        keep_memory=keep_memory,
        total_bytes=path_size(project_path)
        + sum(sp.inventory.total_bytes for sp in session_plans),
    )

    live_blocked = [
        sp for sp in session_plans if sp.blocked_reason and sp.live and sp.live.is_live
    ]
    if live_blocked and not force:
        plan.blocked_reason = (
            f"{len(live_blocked)} live session(s) in project; stop them or pass --force"
        )

    # Also clear tmp scratch for this slug
    from axism.paths import tmp_claude_roots

    for tmp_root in tmp_claude_roots():
        scratch = tmp_root / slug
        if scratch.is_dir():
            plan.extra_paths.append(scratch)
            plan.total_bytes += path_size(scratch)

    return plan


def execute_project_delete(
    plan: ProjectDeletePlan,
    *,
    root: Path | None = None,
    dry_run: bool = True,
    force: bool = False,
    stop_live: bool = True,
) -> list[str]:
    """Delete all sessions in a project, then remove the project directory."""
    root = root or config_root()
    actions: list[str] = []

    if plan.blocked_reason and not force:
        actions.append(f"aborted: {plan.blocked_reason}")
        return actions

    # Delete per-session fragments first (outside the project dir where applicable)
    actions.extend(
        execute_deletes(
            plan.session_plans,
            root=root,
            dry_run=dry_run,
            force=force,
            stop_live=stop_live,
        )
    )

    memory_path = plan.project_path / "memory"
    if plan.keep_memory and memory_path.is_dir():
        # Remove everything except memory/, then remove empty leftovers carefully
        if dry_run:
            actions.append(
                f"would remove project contents under {plan.project_path} (keeping memory/)"
            )
        else:
            for child in list(plan.project_path.iterdir()):
                if child.name == "memory":
                    continue
                try:
                    actions.append(_remove_path(child))
                except OSError as exc:
                    actions.append(f"error removing {child}: {exc}")
    else:
        if dry_run:
            actions.append(
                f"would remove project dir {plan.project_path} ({plan.total_bytes} B)"
            )
        else:
            try:
                actions.append(_remove_path(plan.project_path))
            except OSError as exc:
                actions.append(f"error removing {plan.project_path}: {exc}")

    for extra in plan.extra_paths:
        if dry_run:
            actions.append(f"would remove extra {extra}")
        else:
            try:
                actions.append(_remove_path(extra))
            except OSError as exc:
                actions.append(f"error removing {extra}: {exc}")

    return actions
