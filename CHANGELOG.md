# Changelog

All notable changes to aXism are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Responsive projects/sessions tables: density modes (full/cozy/tight), flex pane ratios with min-widths, agent glyph inlined when narrow, and horizontal scrollbars disabled so small terminals stay keyboard-friendly

### Fixed

- Hermes: commands run against the configured Hermes home are pinned to its default profile, so a sticky active profile can no longer redirect a rename, delete or resume to another profile; renaming to a title that starts with `-` works
- Cursor and Hermes: if a live chat cannot be stopped, its delete aborts (with or without `--force`), a project delete keeps the project, and deleting a session or workspace that does not exist is refused even with `--force`
- Hermes chats started through Hermes' launcher are detected as running, while a terminal multiplexer (tmux, screen, zellij) that merely carries the agent's command line is never mistaken for it or stopped
- Stopping a Cursor or Hermes chat re-checks that the process still belongs to that chat right before signalling it, so a reused process id is never killed
- Cursor: moving a project whose workspace is unknown (a `chats-…` entry) now moves its chats instead of failing
- Cursor and Hermes databases are read correctly when the config path contains characters such as `#` or `?`
- Cursor: deleting a project that does not exist is refused (even with `--force`) instead of touching the path you typed, and a Cursor project delete only removes inside its own `projects/` and `chats/` folders
- Cursor: deleting a project keeps its credentials and agent config (`mcp-auth.json`, `mcps/`, …) and removes the folder only when nothing is left
- A live-session record is trusted only while its process start time still matches, so a reused process id is never listed as live or signalled; duplicate records for one session prefer a running one, then the newest
- If a live session cannot be stopped, its delete aborts with `aborted: could not stop live session` and nothing of it is removed, with or without `--force`
- A session is identified by its transcript file name, so a transcript that mentions another session id is listed and deleted under its own id
- A title set with rename always wins over a generated title
- Renaming a session whose transcript ends mid-line starts a fresh line, so the new title is read back
- Malformed (non-object) JSON in live-session, job and bridge files is skipped instead of crashing discovery
- Deleting a project by `.` or a path resolves it to the matching project only; empty, `..`, `/` or unknown targets are reported as "project not found" and are never overridden by `--force`
- Deletes only remove paths strictly inside the projects, config or scratch roots (anything else is reported as `refused:`), and session ids that are not plain ids match nothing
- Symlinked session fragments are unlinked without touching what they point to
- A session id prefix that matches several sessions is refused with the list of matching ids (exit code 2) instead of acting on the first match, so `delete`, `rename` and similar commands never touch the wrong session
- Moving sessions into an existing project whose folder name contains a hyphen uses that project's real working directory instead of a guessed one, so no stray directory is created
- Moving a transcript that contains a lone emoji surrogate (`\ud83d`) now completes instead of failing and leaving a temp file
- Renaming a project checks the destination workspace first, so a failure leaves the project untouched
- Moving or renaming also updates the working directory in subagent transcripts
- A config directory chosen in Settings now also applies to the Claude Code commands aXism runs (stop, purge, live listing, open/resume), so they act on the same sessions you see
- README links (License, CI workflow, AGENTS.md, CHANGELOG.md) now use absolute GitHub URLs so they resolve correctly from the PyPI project page instead of only working on GitHub

## [0.1.1] — 2026-09-23

### Added

- Cursor provider: IDE agent transcripts (`~/.cursor/projects/<slug>/agent-transcripts/`) and CLI chats (`~/.cursor/chats/<md5-cwd>/<id>/`, both the SQLite and JSON-sidecar shapes), with open, stop, rename, move, and delete
- Hermes provider: interactive sessions from `~/.hermes/state.db`, opened with `hermes --resume` and mutated through `hermes sessions`
- `--provider` flag and `axism providers` command
- Provider `Capabilities`, so keys a backend cannot perform report instead of failing
- Federated inventory: enable multiple backends in Settings; TUI and `axism list` show them together with an **Agent** column when more than one is active
- `providers/federation.py` helpers and composite `provider:id` keys for marks/live maps
- Cross-agent session copy (beta): TUI move to another agent, or `axism export <id> --to-provider … --to /cwd` (text turns; source kept)
- Hermes inventory includes imported (`claude-code` / `codex`) and `oneshot` sessions; exports stamp the destination cwd
- Publish tagged releases to PyPI via GitHub Actions Trusted Publisher (OIDC)
- Project homepage / repository / issues URLs in package metadata

### Changed

- `SessionProvider` covers discovery, liveness, open/stop, fragments, rename, move, and delete; the TUI and CLI no longer reach into Claude Code's modules directly
- `--config-dir` overrides the active provider's root instead of always setting `$CLAUDE_CONFIG_DIR`
- Credentials, agent config files, and backend databases are protected for every provider
- Settings Space = include in inventory; Enter = CLI default (`active_provider`)
- Move/delete refuse mixed-agent selections; destinations stay within the same agent tool
- Sessions filter/sort chrome sits under the sessions table; Tab cycles detail → sessions → projects
- Header shows full brand + version without mid-name ellipsis

### Fixed

- CI pytest collection (`pythonpath` includes project root so `tests.*` imports resolve)
- Cross-agent transfer into Claude Code now writes `customTitle` so the transferred session keeps its title
- Hermes delete stops a live session's process before deleting it, matching Claude Code and Cursor
- Cursor delete now blocks on a genuinely running `cursor-agent` process instead of never blocking
- Cursor session move no longer reports failure when a session is already at the destination

## [0.1.0] — 2026-09-19

Initial public release.

### Added

- Textual TUI and CLI session manager (`axism`) for Claude Code configs
- Cross-project inventory: list, open, stop, rename, move, delete
- Pane-aware projects / sessions / detail layout with essential footer bindings
- Settings for active provider and config directory
- Session filter and sort in the sessions pane
- Safe delete with confirm; project delete uses a stronger danger confirm
- Project move with memory merge and optional agent handoff on dual `MEMORY.md`
- GitHub Actions CI (pytest, ruff, anonymity check) and tagged GitHub Releases
- Install via `scripts/install.sh` / `uv tool install`
