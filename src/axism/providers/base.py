"""Provider protocol for AI coding-agent session backends.

Every backend (Claude Code, Cursor, Hermes) implements the same surface so the
TUI and CLI never reach into one agent's on-disk layout. Operations a backend
cannot perform raise :class:`UnsupportedOperation` and are flagged ahead of
time in :class:`Capabilities`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from axism.delete import DeletePlan, ProjectDeletePlan
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import FragmentInventory
from axism.live import LiveSession
from axism.move import MoveResult


class UnsupportedOperation(RuntimeError):
    """A backend does not implement this operation."""


@dataclass(frozen=True)
class Capabilities:
    """What a backend can actually do, for UI gating and clear errors."""

    resume: bool = True
    slash_stop: bool = True
    stop: bool = True
    rename: bool = True
    move_sessions: bool = True
    move_projects: bool = True
    delete_sessions: bool = True
    delete_projects: bool = True
    fragments: bool = True
    memory_merge: bool = False
    purge_project: bool = False


@runtime_checkable
class SessionProvider(Protocol):
    """Discover, enrich, open, and mutate sessions for one agent tool."""

    name: str
    label: str
    capabilities: Capabilities
    config_hint: str

    def config_root(self) -> Path:
        """Root directory where this provider stores sessions."""
        ...

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        ...

    def find_session(
        self, session_id: str, root: Path | None = None
    ) -> tuple[ProjectInfo, SessionMeta] | None:
        ...

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        ...

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        """Argv plus working directory used to hand off to the agent CLI."""
        ...

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        """Replace the current process with attach or resume of ``session``."""
        ...

    def can_stop(self, live: LiveSession | None) -> bool:
        """Whether Stop can act on this live record for this backend."""
        ...

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """Soft stop of a live background session."""
        ...

    def stop(self, session_id: str, live: LiveSession | None = None) -> tuple[bool, str]:
        """Harder stop/kill of a live session."""
        ...

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        ...

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        ...

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        ...

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        ...

    def plan_delete(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        force: bool = False,
        use_cli: bool = True,
    ) -> DeletePlan:
        ...

    def execute_deletes(
        self,
        plans: list[DeletePlan],
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        ...

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        ...

    def execute_project_delete(
        self,
        plan: ProjectDeletePlan,
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        ...

    def purge_project(
        self, path: str | None = None, *, dry_run: bool = True, all_projects: bool = False
    ) -> tuple[int, str]:
        ...


class ProviderBase:
    """Default implementations that refuse unsupported operations.

    Backends subclass this and override only what their agent supports.
    """

    name = "unknown"
    label = "Unknown"
    capabilities = Capabilities(
        resume=False,
        slash_stop=False,
        stop=False,
        rename=False,
        move_sessions=False,
        move_projects=False,
        delete_sessions=False,
        delete_projects=False,
        fragments=False,
    )
    config_hint = ""

    def _unsupported(self, operation: str) -> UnsupportedOperation:
        return UnsupportedOperation(f"{self.label} does not support {operation}")

    def config_root(self) -> Path:
        raise self._unsupported("a config root")

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        raise self._unsupported("discovery")

    def find_session(
        self, session_id: str, root: Path | None = None
    ) -> tuple[ProjectInfo, SessionMeta] | None:
        """Match on exact id first, then unique-ish prefix."""
        needle = session_id.lower()
        for proj in self.discover(root):
            for sess in proj.sessions:
                sid = sess.session_id.lower()
                if sid == needle or sid.startswith(needle):
                    return proj, sess
        return None

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        return {}

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        raise self._unsupported("opening sessions")

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        raise self._unsupported("opening sessions")

    def can_stop(self, live: LiveSession | None) -> bool:
        if live is None or not self.capabilities.stop:
            return False
        return live.is_killable

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        return False, f"{self.label} has no per-session stop"

    def stop(self, session_id: str, live: LiveSession | None = None) -> tuple[bool, str]:
        return False, f"{self.label} has no per-session stop"

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        return FragmentInventory(session_id=session_id, project_slug=project_slug)

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        raise self._unsupported("renaming sessions")

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        return MoveResult(False, f"{self.label} does not support moving sessions")

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        return MoveResult(False, f"{self.label} does not support moving projects")

    def plan_delete(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        force: bool = False,
        use_cli: bool = True,
    ) -> DeletePlan:
        raise self._unsupported("deleting sessions")

    def execute_deletes(
        self,
        plans: list[DeletePlan],
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        raise self._unsupported("deleting sessions")

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        raise self._unsupported("deleting projects")

    def execute_project_delete(
        self,
        plan: ProjectDeletePlan,
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        raise self._unsupported("deleting projects")

    def purge_project(
        self, path: str | None = None, *, dry_run: bool = True, all_projects: bool = False
    ) -> tuple[int, str]:
        return 1, f"{self.label} has no project purge command"
