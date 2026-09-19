"""Discover local coding-agent CLIs and prepare memory-merge handoff briefs."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from axism.move import MemoryStats, MoveResult
from axism.paths import config_root

# Preference order for --memory-agent auto
AGENT_PREFERENCE = ("claude", "cursor", "opencode", "aider", "codex")


@dataclass(frozen=True)
class AgentCLI:
    id: str
    label: str
    binary: str

    def handoff_argv(self, brief_path: Path) -> list[str]:
        """Build argv to start this agent with the merge brief (no shell)."""
        brief = str(brief_path)
        prompt = (
            f"Read and follow the task brief at {brief}. "
            "Stay within the boundaries listed there."
        )
        if self.id == "claude":
            return [self.binary, "-p", prompt]
        if self.id == "aider":
            return [self.binary, "--message", prompt]
        if self.id == "opencode":
            return [self.binary, "run", prompt]
        if self.id == "codex":
            return [self.binary, "exec", prompt]
        if self.id == "cursor":
            # Best-effort; many installs only expose the GUI binary.
            return [self.binary, "agent", prompt]
        return [self.binary, prompt]


def _which_first(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def detect_agents() -> list[AgentCLI]:
    """Return agent CLIs available on PATH, in preference order."""
    specs: list[tuple[str, str, tuple[str, ...]]] = [
        ("claude", "Claude Code", ("claude",)),
        ("cursor", "Cursor agent", ("cursor-agent", "cursor")),
        ("opencode", "OpenCode", ("opencode",)),
        ("aider", "Aider", ("aider",)),
        ("codex", "Codex", ("codex",)),
    ]
    out: list[AgentCLI] = []
    for aid, label, bins in specs:
        binary = _which_first(*bins)
        if binary:
            out.append(AgentCLI(id=aid, label=label, binary=binary))
    return out


def pick_auto_agent(agents: list[AgentCLI] | None = None) -> AgentCLI | None:
    agents = agents if agents is not None else detect_agents()
    by_id = {a.id: a for a in agents}
    for aid in AGENT_PREFERENCE:
        if aid in by_id:
            return by_id[aid]
    return agents[0] if agents else None


def brief_path(root: Path | None = None) -> Path:
    root = root or config_root()
    return root / ".axism-memory-merge-task.md"


def write_memory_merge_brief(
    result: MoveResult,
    *,
    root: Path | None = None,
    force: bool = False,
) -> Path:
    """Write an agent-agnostic task brief for finishing MEMORY.md merge."""
    root = root or config_root()
    path = brief_path(root)
    dest = result.memory_dest
    stats = result.memory_stats or MemoryStats()
    parked = "\n".join(f"- `{p}`" for p in result.memory_parked) or "- (none)"
    renamed = "\n".join(f"- `{p}`" for p in result.memory_renamed) or "- (none)"
    force_note = (
        "\n**User overrode size/complexity limits.** Still refuse if you cannot "
        "complete a safe merge without inventing content or touching other paths.\n"
        if force
        else ""
    )
    body = f"""# aXism memory merge task

Merge project memory indexes after an aXism project move.
{force_note}
## Paths

- Destination memory dir: `{dest}`
- Parked source MEMORY files:
{parked}
- Renamed collision topic files:
{renamed}

Stats: {stats.summary()}

## Goal

1. Produce one coherent `MEMORY.md` in the destination memory dir.
2. Keep all topic `.md` files; prefer destination wording when deduping.
3. Ensure every topic file is linked from `MEMORY.md`.
4. Do not invent facts; only reorganize and reconcile existing markdown.

## Hard boundaries

- Only read/write files under the destination memory directory listed above
  (including parked `MEMORY-from-*.md` files already moved there).
- Do **not** edit session `.jsonl` transcripts, jobs, credentials, settings,
  skills, plugins, or anything outside that memory directory.
- Do **not** use the network or make git commits.
- Do **not** delete the destination `MEMORY.md` without replacing it.

## Success criteria

- Single canonical `MEMORY.md` in the destination memory dir.
- No orphaned topic files without an index link.
- Parked `MEMORY-from-*.md` files may be removed only after their content is
  merged into the canonical index (or clearly noted as obsolete).

## If too hard

Stop. Write `MERGE_ABORTED.md` in the destination memory dir explaining why,
and leave other files unchanged as much as possible.
"""
    path.write_text(body, encoding="utf-8")
    return path


def exec_agent_handoff(agent: AgentCLI, brief: Path) -> None:
    """Replace this process with the chosen agent CLI. Never returns on success."""
    argv = agent.handoff_argv(brief)
    os.execvp(argv[0], argv)
