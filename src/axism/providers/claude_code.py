"""Claude Code session provider (first backend)."""

from __future__ import annotations

from pathlib import Path

from axism.delete import (
    DeletePlan,
    ProjectDeletePlan,
    build_delete_plan,
    build_project_delete_plan,
    execute_deletes,
    execute_project_delete,
)
from axism.discover import ProjectInfo, SessionMeta, discover_all, find_session
from axism.fragments import FragmentInventory, collect_fragments
from axism.live import (
    LiveSession,
    exec_open,
    kill_live_session,
    merge_live,
    open_command,
    purge_project_cli,
    slash_stop,
)
from axism.move import MoveResult, move_projects, move_sessions
from axism.paths import config_root as claude_config_root
from axism.providers.base import Capabilities, ProviderBase
from axism.rename import rename_session


class ClaudeCodeProvider(ProviderBase):
    """Sessions under ``$CLAUDE_CONFIG_DIR`` / ``~/.claude`` (or a settings override)."""

    name = "claude_code"
    label = "Claude Code"
    config_hint = "$CLAUDE_CONFIG_DIR or ~/.claude"
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
        memory_merge=True,
        purge_project=True,
    )

    def __init__(self, config_dir: Path | str | None = None) -> None:
        self._config_dir: Path | None
        if config_dir is None:
            self._config_dir = None
        else:
            self._config_dir = Path(config_dir).expanduser()

    def config_root(self) -> Path:
        if self._config_dir is not None:
            return self._config_dir.resolve()
        return claude_config_root()

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        return discover_all(root or self.config_root())

    def find_session(
        self, session_id: str, root: Path | None = None
    ) -> tuple[ProjectInfo, SessionMeta] | None:
        return find_session(session_id, root or self.config_root())

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        return merge_live(root or self.config_root(), use_cli=use_cli)

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        return open_command(session.session_id, cwd=session.cwd, live=live)

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        """Attach to active background jobs; otherwise ``claude --resume``."""
        exec_open(session.session_id, cwd=session.cwd, live=live)

    def can_stop(self, live: LiveSession | None) -> bool:
        """Only background jobs: ``/stop`` does not exist for interactive sessions."""
        if live is None or live.kind != "background":
            return False
        return live.state in {"working", "blocked", "running"} or live.is_killable

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """Same as typing ``/stop`` while attached to a background session."""
        return slash_stop(session_id, live, root=self.config_root())

    def stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        return kill_live_session(
            session_id, live, root=self.config_root(), use_cli=True
        )

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        return collect_fragments(
            session_id, project_slug=project_slug, root=self.config_root()
        )

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        return rename_session(session_id, title, root=self.config_root())

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        return move_sessions(
            sessions, dest, root=self.config_root(), dry_run=dry_run
        )

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        return move_projects(
            projects,
            dest,
            root=self.config_root(),
            dry_run=dry_run,
            merge_memory=merge_memory,
        )

    def plan_delete(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        force: bool = False,
        use_cli: bool = True,
    ) -> DeletePlan:
        return build_delete_plan(
            session_id,
            project_slug=project_slug,
            root=self.config_root(),
            force=force,
            use_cli=use_cli,
        )

    def execute_deletes(
        self,
        plans: list[DeletePlan],
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        return execute_deletes(
            plans,
            root=self.config_root(),
            dry_run=dry_run,
            force=force,
            stop_live=stop_live,
        )

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        return build_project_delete_plan(
            slug_or_path,
            root=self.config_root(),
            force=force,
            use_cli=use_cli,
            keep_memory=keep_memory,
        )

    def execute_project_delete(
        self,
        plan: ProjectDeletePlan,
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        return execute_project_delete(
            plan,
            root=self.config_root(),
            dry_run=dry_run,
            force=force,
            stop_live=stop_live,
        )

    def purge_project(
        self, path: str | None = None, *, dry_run: bool = True, all_projects: bool = False
    ) -> tuple[int, str]:
        return purge_project_cli(path, dry_run=dry_run, all_projects=all_projects)


def default_provider() -> ClaudeCodeProvider:
    return ClaudeCodeProvider()
