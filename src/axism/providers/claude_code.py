"""Claude Code session provider (first backend)."""

from __future__ import annotations

from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta, discover_all
from axism.live import LiveSession, exec_open, kill_live_session, merge_live, slash_stop
from axism.paths import config_root as claude_config_root


class ClaudeCodeProvider:
    """Sessions under ``$CLAUDE_CONFIG_DIR`` / ``~/.claude`` (or a settings override)."""

    name = "claude_code"
    label = "Claude Code"

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

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        return merge_live(root or self.config_root(), use_cli=use_cli)

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        """Attach to active background jobs; otherwise ``claude --resume``."""
        exec_open(session.session_id, cwd=session.cwd, live=live)

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


def default_provider() -> ClaudeCodeProvider:
    return ClaudeCodeProvider()
