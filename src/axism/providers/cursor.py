"""Cursor session provider (IDE agent transcripts + CLI chats)."""

from __future__ import annotations

import shutil
from pathlib import Path

from axism.delete import DeletePlan, ProjectDeletePlan
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import FragmentInventory
from axism.live import LiveSession
from axism.move import MoveResult
from axism.paths import path_size
from axism.providers.base import Capabilities, ProviderBase
from axism.providers.common import (
    exec_with_cwd,
    execute_fragment_deletes,
    remove_path,
    terminate_pid,
)
from axism.providers.cursor_store import discover as cursor_discover
from axism.providers.cursor_store import fragments as cursor_fragments
from axism.providers.cursor_store import live as cursor_live
from axism.providers.cursor_store import ops as cursor_ops
from axism.providers.cursor_store.paths import (
    NON_PROJECT_DIRS,
    chats_dir,
    cursor_data_dir,
    cwd_bucket,
    decode_slug,
    projects_dir,
    slugify_path,
)

AGENT_BINARY = "cursor-agent"


class CursorProvider(ProviderBase):
    """Chats under ``$CURSOR_DATA_DIR`` / ``~/.cursor`` (Linux and macOS alike)."""

    name = "cursor"
    label = "Cursor"
    config_hint = "$CURSOR_DATA_DIR or ~/.cursor"
    capabilities = Capabilities(
        resume=True,
        slash_stop=True,
        stop=True,
        rename=True,
        move_sessions=True,
        move_projects=True,
        delete_sessions=True,
        delete_projects=True,
        fragments=True,
        memory_merge=False,
        purge_project=False,
    )

    def __init__(self, config_dir: Path | str | None = None) -> None:
        self._config_dir = (
            Path(config_dir).expanduser() if config_dir is not None else None
        )

    def config_root(self) -> Path:
        if self._config_dir is not None:
            return self._config_dir.resolve()
        return cursor_data_dir()

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        return cursor_discover.discover_all(root or self.config_root())

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        ids = {
            s.session_id
            for project in self.discover(root)
            for s in project.sessions
        }
        return cursor_live.merge_live(ids)

    # -- open -------------------------------------------------------------

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        if not shutil.which(AGENT_BINARY):
            raise FileNotFoundError(f"{AGENT_BINARY} not found on PATH")
        return [AGENT_BINARY, "--resume", session.session_id], session.cwd or None

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        argv, workdir = self.open_command(session, live)
        exec_with_cwd(argv, workdir)

    # -- stop -------------------------------------------------------------

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """Cursor has no soft stop; terminate the agent process running this chat."""
        return self.stop(session_id, live)

    def stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        if live is None:
            live = cursor_live.merge_live({session_id}).get(session_id)
        if live is None or live.pid is None:
            return False, f"no running cursor-agent process for {session_id[:8]}…"
        return terminate_pid(live.pid)

    # -- inventory / mutations -------------------------------------------

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        return cursor_fragments.collect_fragments(
            self.config_root(), session_id, project_slug=project_slug
        )

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        return cursor_ops.rename_session(self.config_root(), session_id, title)

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        return cursor_ops.move_sessions(
            self.config_root(), sessions, dest, dry_run=dry_run
        )

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        return cursor_ops.move_projects(
            self.config_root(), projects, dest, dry_run=dry_run
        )

    # -- delete -----------------------------------------------------------

    def plan_delete(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        force: bool = False,
        use_cli: bool = True,
    ) -> DeletePlan:
        root = self.config_root()
        full_id = session_id
        if not cursor_discover.locate_sessions(root, session_id):
            found = self.find_session(session_id)
            if not found:
                return DeletePlan(
                    session_id=session_id,
                    inventory=FragmentInventory(
                        session_id=session_id, project_slug=project_slug
                    ),
                    blocked_reason="session not found",
                )
            _proj, sess = found
            full_id = sess.session_id
            project_slug = project_slug or sess.project_slug

        inventory = self.collect_fragments(full_id, project_slug=project_slug)
        live = cursor_live.merge_live({full_id}).get(full_id)
        plan = DeletePlan(session_id=full_id, inventory=inventory, live=live)
        if live is not None and live.is_killable and not force:
            plan.blocked_reason = (
                f"cursor-agent still running (pid={live.pid}); stop it or use --force"
            )
        return plan

    def execute_deletes(
        self,
        plans: list[DeletePlan],
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        return execute_fragment_deletes(
            plans,
            dry_run=dry_run,
            force=force,
            stop_live=stop_live,
            stop_fn=self.stop,
        )

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        root = self.config_root()
        projects = {p.slug: p for p in self.discover(root)}
        project = projects.get(slug_or_path)
        if project is None:
            slug = slugify_path(Path(slug_or_path).expanduser())
            project = projects.get(slug)
        if project is None:
            return ProjectDeletePlan(
                slug=slug_or_path,
                project_path=projects_dir(root) / slug_or_path,
                blocked_reason=f"project not found: {slug_or_path}",
            )

        project_path = projects_dir(root) / project.slug
        plan = ProjectDeletePlan(
            slug=project.slug,
            project_path=project_path,
            session_plans=[
                self.plan_delete(
                    s.session_id, project_slug=project.slug, force=force
                )
                for s in project.sessions
            ],
        )
        if project_path.is_dir():
            plan.total_bytes += path_size(project_path)
        cwd = project.cwd_guess if not project.slug.startswith("chats-") else ""
        bucket = (
            chats_dir(root) / cwd_bucket(cwd)
            if cwd
            else chats_dir(root) / project.slug.removeprefix("chats-")
        )
        if bucket.is_dir():
            plan.extra_paths.append(bucket)
            plan.total_bytes += path_size(bucket)

        live_blocked = [
            sp for sp in plan.session_plans if sp.blocked_reason and sp.live
        ]
        if live_blocked and not force:
            plan.blocked_reason = (
                f"{len(live_blocked)} chat(s) still running; stop them or use --force"
            )
        return plan

    def execute_project_delete(
        self,
        plan: ProjectDeletePlan,
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        actions: list[str] = []
        if plan.blocked_reason and not force:
            return [f"aborted: {plan.blocked_reason}"]

        if plan.session_plans:
            actions.extend(
                self.execute_deletes(
                    plan.session_plans,
                    dry_run=dry_run,
                    force=force,
                    stop_live=stop_live,
                )
            )

        for target in [plan.project_path, *plan.extra_paths]:
            if not target.is_dir():
                continue
            if dry_run:
                actions.append(f"would remove {target} ({path_size(target)} B)")
                continue
            try:
                actions.append(remove_path(target))
            except OSError as exc:
                actions.append(f"error removing {target}: {exc}")
        return actions


def workspace_candidates(root: Path | None = None) -> list[str]:
    """Workspace paths Cursor knows about (used by tests and diagnostics)."""
    base = root or cursor_data_dir()
    pdir = projects_dir(base)
    if not pdir.is_dir():
        return []
    return [
        decode_slug(child.name)
        for child in sorted(pdir.iterdir())
        if child.is_dir() and child.name not in NON_PROJECT_DIRS
    ]
