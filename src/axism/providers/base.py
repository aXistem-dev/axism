"""Provider protocol for AI coding-agent session backends.

Every backend (Claude Code, Cursor, Hermes) implements the same surface so the
TUI and CLI never reach into one agent's on-disk layout. Operations a backend
cannot perform raise :class:`UnsupportedOperation` and are flagged ahead of
time in :class:`Capabilities`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from axism.delete import DeletePlan, ProjectDeletePlan
from axism.details import SessionDetails, TranscriptUnavailable
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import FragmentInventory
from axism.ids import match_session_id
from axism.live import LiveSession
from axism.move import MoveResult

if TYPE_CHECKING:
    from axism.providers.transfer import Turn


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
    # True when sessions live in the agent's own database rather than in
    # transcript files (the details pane says so instead of "transcript").
    stores_in_database: bool

    def config_root(self) -> Path:
        """Root directory where this provider stores sessions."""
        ...

    def detect(self) -> bool:
        """Whether this agent appears to be installed or used on this machine."""
        ...

    def status_note(self) -> str:
        """Short health note shown by ``axism providers`` (empty when none)."""
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

    def transcript(self, session: SessionMeta) -> list[Turn]:
        """Text turns of the conversation; raises ``TranscriptUnavailable``."""
        ...

    def session_details(self, session: SessionMeta) -> SessionDetails:
        """Facts, first/last prompt and last reply; never raises."""
        ...


_TOOL_RESULT_LINE = "[tool result]"
_TOOL_USE_RE = re.compile(r"^\[ran tool: .*\]$")


def text_turns(turns: list[Turn]) -> list[Turn]:
    """Keep only what was said: drop tool placeholders and merge same-role runs."""
    from axism.providers.transfer import Turn  # lazy: transfer imports providers

    out: list[Turn] = []
    for turn in turns:
        lines = [
            ln
            for ln in turn.content.splitlines()
            if ln.strip() != _TOOL_RESULT_LINE and not _TOOL_USE_RE.match(ln.strip())
        ]
        text = "\n".join(lines).strip()
        if not text:
            continue
        if out and out[-1].role == turn.role:
            out[-1].content = f"{out[-1].content}\n{text}"
        else:
            out.append(Turn(role=turn.role, content=text))
    return out


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
    stores_in_database = False

    def detect(self) -> bool:
        return False

    def status_note(self) -> str:
        return ""

    def _unsupported(self, operation: str) -> UnsupportedOperation:
        return UnsupportedOperation(f"{self.label} does not support {operation}")

    def config_root(self) -> Path:
        raise self._unsupported("a config root")

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        raise self._unsupported("discovery")

    def find_session(
        self, session_id: str, root: Path | None = None
    ) -> tuple[ProjectInfo, SessionMeta] | None:
        """Match on exact id first, then a unique prefix; ambiguity raises."""
        found = [(proj, sess) for proj in self.discover(root) for sess in proj.sessions]
        sid = match_session_id([s.session_id for _, s in found], session_id)
        if sid is None:
            return None
        return next((p, s) for p, s in found if s.session_id == sid)

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

    def transcript(self, session: SessionMeta) -> list[Turn]:
        # Imported lazily: transfer imports the provider modules.
        from axism.providers import transfer

        try:
            canonical = transfer.export_session(self, session)
        except Exception as exc:
            raise TranscriptUnavailable(f"Cannot read conversation: {exc}") from exc
        if canonical.export_error:
            raise TranscriptUnavailable(canonical.export_error)
        return text_turns(canonical.turns)

    def session_details(self, session: SessionMeta) -> SessionDetails:
        details = SessionDetails()
        try:
            turns = self.transcript(session)
        except TranscriptUnavailable as exc:
            details.note = exc.reason
            return details
        details.turns = len(turns)
        users = [t.content for t in turns if t.role == "user"]
        replies = [t.content for t in turns if t.role == "assistant"]
        details.first_prompt = users[0] if users else None
        details.last_prompt = users[-1] if users else None
        details.last_reply = replies[-1] if replies else None
        return details
