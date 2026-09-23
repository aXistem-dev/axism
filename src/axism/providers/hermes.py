"""Hermes (Nous Research) session provider — default profile chats."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from axism.delete import DeletePlan, ProjectDeletePlan
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import FragmentInventory
from axism.live import LiveSession
from axism.move import MoveResult
from axism.providers.base import Capabilities, ProviderBase
from axism.providers.common import (
    exec_with_cwd,
    find_agent_sessions,
    remove_path,
    terminate_pid,
)
from axism.providers.hermes_store import discover as hermes_discover
from axism.providers.hermes_store import fragments as hermes_fragments
from axism.providers.hermes_store import ops as hermes_ops
from axism.providers.hermes_store.paths import hermes_home, state_db


class HermesProvider(ProviderBase):
    """Sessions in ``$HERMES_HOME/state.db`` (``~/.hermes`` on Linux and macOS).

    Profiles, kanban boards, and ``hermes project`` workspaces are separate
    stores and are not managed here.
    """

    name = "hermes"
    label = "Hermes"
    config_hint = "$HERMES_HOME or ~/.hermes"
    capabilities = Capabilities(
        resume=True,
        slash_stop=True,
        stop=True,
        rename=True,
        move_sessions=False,
        move_projects=False,
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
        return hermes_home()

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        return hermes_discover.discover_all(root or self.config_root())

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        """Sessions named by a running ``hermes`` process.

        Hermes has no per-session liveness registry, so this is a heuristic and
        never a reason to block a delete on its own.
        """
        ids = {
            s.session_id for project in self.discover(root) for s in project.sessions
        }
        return {
            session_id: LiveSession(
                session_id=session_id,
                kind="interactive",
                state="running",
                pid=pid,
                source="filesystem",
            )
            for session_id, pid in find_agent_sessions(ids, {hermes_ops.BINARY}).items()
        }

    # -- open -------------------------------------------------------------

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        if not shutil.which(hermes_ops.BINARY):
            raise FileNotFoundError(f"{hermes_ops.BINARY} not found on PATH")
        return hermes_ops.resume_argv(session.session_id), session.cwd or None

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        argv, workdir = self.open_command(session, live)
        os.environ["HERMES_HOME"] = str(self.config_root())
        exec_with_cwd(argv, workdir)

    # -- stop -------------------------------------------------------------

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """Hermes has no per-session stop; end the process running this chat.

        ``hermes pause`` is a global emergency stop and is never used here.
        """
        return self.stop(session_id, live)

    def stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        if live is None:
            live = self.merge_live().get(session_id)
        if live is None or live.pid is None:
            return False, f"no running hermes process for {session_id}"
        return terminate_pid(live.pid)

    # -- inventory / mutations -------------------------------------------

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        return hermes_fragments.collect_fragments(
            self.config_root(), session_id, project_slug=project_slug
        )

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        return hermes_ops.rename_session(self.config_root(), session_id, title)

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        return MoveResult(
            False,
            "Hermes has no session move: a session's workspace is recorded in "
            "state.db by the agent itself",
        )

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        return MoveResult(
            False, "Hermes workspaces are derived from session cwd and cannot be moved"
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
        if not hermes_discover.session_exists(root, session_id):
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
        live = self.merge_live().get(full_id)
        plan = DeletePlan(session_id=full_id, inventory=inventory, live=live)
        if live is not None and not force:
            plan.blocked_reason = (
                f"a hermes process is using this session (pid={live.pid}); "
                "close it or use --force"
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
        if not plans:
            return ["nothing to delete"]
        blocked = [p for p in plans if p.blocked_reason and not force]
        if blocked:
            return [f"aborted {p.session_id}: {p.blocked_reason}" for p in blocked]

        root = self.config_root()
        actions: list[str] = []
        for plan in plans:
            live = plan.live
            if stop_live and live is not None and live.is_killable:
                if dry_run:
                    actions.append(
                        f"{plan.session_id[:8]}: would stop live session "
                        f"(state={live.state})"
                    )
                else:
                    ok, msg = self.stop(plan.session_id, live)
                    actions.append(f"{plan.session_id[:8]}: stop: {msg}")
                    if not ok and not force:
                        actions.append(
                            f"{plan.session_id[:8]}: aborted: could not stop live session"
                        )
                        continue

            if dry_run:
                actions.append(
                    f"would run hermes sessions delete {plan.session_id} --yes"
                )
            else:
                ok, msg = hermes_ops.delete_session(root, plan.session_id)
                actions.append(f"{plan.session_id}: {msg}")
                if not ok and not force:
                    continue
            for frag in plan.inventory.deletable:
                if dry_run:
                    actions.append(f"would remove [{frag.kind}] {frag.path}")
                    continue
                try:
                    actions.append(remove_path(frag.path))
                except OSError as exc:
                    actions.append(f"error removing {frag.path}: {exc}")
        return actions

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        """Delete every session in one workspace group; Hermes has no project dir."""
        root = self.config_root()
        projects = {p.slug: p for p in self.discover(root)}
        project = projects.get(slug_or_path)
        if project is None:
            resolved = str(Path(slug_or_path).expanduser())
            project = projects.get(resolved)
        if project is None:
            return ProjectDeletePlan(
                slug=slug_or_path,
                project_path=state_db(root),
                blocked_reason=f"workspace not found: {slug_or_path}",
            )

        plan = ProjectDeletePlan(
            slug=project.slug,
            project_path=state_db(root),
            session_plans=[
                self.plan_delete(s.session_id, project_slug=project.slug, force=force)
                for s in project.sessions
            ],
            total_bytes=project.total_bytes,
        )
        live_blocked = [sp for sp in plan.session_plans if sp.blocked_reason and sp.live]
        if live_blocked and not force:
            plan.blocked_reason = (
                f"{len(live_blocked)} session(s) in use; close them or use --force"
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
        if plan.blocked_reason and not force:
            return [f"aborted: {plan.blocked_reason}"]
        if not plan.session_plans:
            return [f"workspace {plan.slug} has no sessions"]
        return self.execute_deletes(
            plan.session_plans, dry_run=dry_run, force=force, stop_live=stop_live
        )
