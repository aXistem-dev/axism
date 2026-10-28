# Changelog

All notable changes to aXism are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-10-28

OpenCode 2 joins Claude Code, Cursor and Hermes as a fourth agent, aXism switches on the agents it finds installed instead of all of them, and the keyboard, footer, help, details pane and Settings are reworked.

### Added

- OpenCode 2 provider (`--provider opencode`): sessions from `opencode.db` in the OpenCode data folder (`$XDG_DATA_HOME/opencode`, default `~/.local/share/opencode`), grouped by project, with title, first prompt, size, last update, running state and an `archived` badge; sub-agent sessions are counted with their parent
- Open, rename and delete OpenCode sessions and projects; every change goes through OpenCode's own server (the background service when it runs, otherwise a private server for that command), and listing never starts anything
- Stop an OpenCode session: interrupts it and closes any OpenCode window that has it open; a running or open sub-agent session makes its parent count as running, and stopping it interrupts the whole family
- Move OpenCode sessions and projects to another folder while OpenCode's background service runs on the default data folder; without it, or with a custom data folder, the move is refused before anything is stopped
- Copy sessions from OpenCode to Claude Code, Cursor and Hermes, and into OpenCode through `opencode session import` (the copy gets a new session id), text turns only, title kept
- A custom OpenCode data folder in Settings or with `--config-dir`; it must be named `opencode` for changes to work, and the command that opens one of its sessions (`axism resume --print-only`) sets `XDG_DATA_HOME` to its parent folder
- `axism providers` shows whether the OpenCode database was found and supported and whether its background service is running; an unsupported (non-OpenCode 2) layout is reported in one line
- Vim keys (`j`/`k`, `h`/`l`, `g`/`G`, `Ctrl+D`/`Ctrl+U`) next to the arrows, `PgUp`/`PgDn`, `Home`/`End`, and `Del` for delete; capital letters do the same as lowercase, except `G` (last row) and `T` (next theme)
- `Ctrl+P` opens a command palette that runs any action by name
- `Enter` goes into the highlighted item (projects, sessions, details) and opens a full-screen reader with the whole conversation (text turns only) from the details pane; `Esc` goes back
- The details pane shows a session's facts first, then its last prompt and last reply
- A footer that follows the focused pane, dims keys that do not apply there, and says why when you press one
- A help screen (`?`) generated from the same key list the app uses, so it always matches what the keys do
- Settings: `Space` switches an agent on or off, `*` makes it the CLI default, `Enter` saves; each agent shows its status (sessions found, folder missing, command not installed) and saving warns about a folder that does not exist
- `axism providers --default`, `--enable` and `--disable` change the agents and the CLI default, and the list marks the default with `*` and a legend line

### Changed

- `?` opens the help (`h` moves to the pane on the left); `q` asks before dropping marks
- Dialogs fit small terminals and scroll when their text is longer than the window
- `axism tui --provider` and `--config-dir` apply to that run only
- Agents without a saved choice are detected once (data folder with content, or the command on `PATH`) and switched on or off accordingly, instead of all being switched on; saved choices are never changed, and Claude Code is shown if no agent ends up switched on. The note names the agents found and the ones not detected (in the app as a message, on stderr for other commands); a `settings.json` that cannot be read is never overwritten, and the detected choices then apply to that run only
- GitHub releases use this changelog's section for the version as their release notes

## [0.1.2] — 2026-10-27

### Added

- `k` in the project delete dialog keeps the project's `memory/` folder (Claude Code); the dialog says whether it will be deleted or kept and updates as you toggle
- `axism move` refuses sessions (and projects containing sessions) that are running, as the app does; `--force` overrides
- A slug that starts with a dash is passed after `--`, for example `axism delete-project -- -home-alice-src-demo`

### Changed

- Exit codes for `delete` and `delete-project`: without `--yes` or `--dry-run` they print the preview and exit 1; an explicit `--dry-run` exits 0; a blocked plan (not found, or a live session without `--force`) exits 2 in every mode; a `--yes` run that reports an aborted, refused or failed step exits 1
- aXistem is named as author and copyright holder
- Responsive projects/sessions tables: density modes (full/cozy/tight), flex pane ratios with minimum widths, the agent glyph inlined when narrow, and horizontal scrollbars disabled so small terminals stay keyboard-friendly
- Narrow terminals (under 70 columns) hide the details pane so both lists stay visible, table columns count their cell padding so rows fit without sideways scrolling, and the first screen uses the real terminal width
- A config directory chosen in Settings also applies to the Claude Code commands aXism runs (stop, purge, live listing, open/resume), so they act on the same sessions you see
- The release workflow stops when the tag does not match `pyproject.toml` or `__version__`, and runs the tests, lint and anonymity check before building and publishing
- README links (License, CI workflow, AGENTS.md, CHANGELOG.md) use absolute GitHub URLs so they resolve from the PyPI project page

### Fixed

#### Delete safety

- A session that cannot be stopped is never deleted: its delete aborts with `aborted: could not stop live session` and nothing of it is removed, with or without `--force`. A project delete then keeps the project folder and reports how many other sessions were deleted
- A session or project that does not exist is refused even with `--force`, in every backend; this includes a full session id with nothing left on disk, while leftover files of a missing session can still be cleaned up
- Session ids that are not plain ids (`.`, `..`, empty, containing `/`) match nothing. In Claude Code, `delete-project` takes a slug or a path, so `.` and `..` mean the current and parent directory; a path that does not belong to a known project, the agent's own folders, and `/` are refused as "project not found", even with `--force`. A full session id is checked with a direct file lookup and compared as a canonical UUID (case-insensitive), so planning a delete stays fast in large homes
- Deletes only remove paths strictly inside the backend's own roots (anything else is reported as `refused:`). Symlinked files, stores and project folders are never followed: a symlinked file or store is unlinked without touching its target, and a symlinked project folder is refused
- A live-session record is trusted only while its process start time still matches, so a reused process id is never listed as live or signalled; duplicate records for one session prefer a running one, then the newest. Stopping a Cursor or Hermes chat re-checks the process right before signalling it
- Deleting a Hermes session fails (and keeps its legacy files) when the `hermes` command fails, instead of reporting success
- Deleting a Cursor project keeps its credentials and agent config (`mcp-auth.json`, `mcps/`, ...) and removes the folder only when nothing is left
- A session is identified by its transcript file name, so a transcript that mentions another session id is listed and deleted under its own id
- A session id prefix that matches several sessions is refused with the list of matching ids (exit code 2), so `delete`, `rename` and similar commands never touch the wrong session

#### App

- The app reports a delete as failed when a session could not be stopped, was aborted, or a path was refused, instead of showing success
- `a` in the sessions pane marks only the sessions shown by the current filter, so a filtered delete cannot remove hidden sessions
- Deleting the last project clears the selected session and the detail pane, so `o` and `n` never act on a session that is gone
- Left and Right move the text cursor while typing in the session filter instead of switching panes
- Toggling `k` in the project delete dialog does not re-query running sessions

#### Backends

- Hermes: commands run against the configured Hermes home use its default profile, so a sticky active profile cannot redirect a rename, delete or resume; renaming to a title that starts with `-` works
- Hermes chats started through Hermes' launcher are detected as running, while a terminal multiplexer (tmux, screen, zellij) that merely carries the agent's command line is never mistaken for one
- Cursor: moving a project whose workspace is unknown (a `chats-...` entry) moves its chats instead of failing
- Cursor and Hermes databases are read correctly when the config path contains characters such as `#` or `?`
- Copying a Hermes session to another agent fails cleanly (and exits non-zero) when the Hermes export fails, instead of writing a stub session; a relative `--to` resolves against the current directory, an empty one is an error, and a custom title is carried over from Claude Code transcripts
- `--config-dir` works when the saved active backend is unknown or disabled (it falls back to Claude Code like a plain run), and a settings file that is not valid UTF-8 or has a non-boolean `enabled` starts normally

#### Moving, renaming and discovery

- Moving sessions into an existing project uses that project's real working directory, taken from its transcripts only when it encodes back to the project folder name, so no stray directory is created
- Moving a transcript that contains a lone emoji surrogate completes instead of failing and leaving a temp file
- Moving or renaming also updates the working directory in subagent transcripts, and renaming a project checks the destination workspace first so a failure leaves it untouched
- A title set with rename always wins over a generated title, and renaming a session whose transcript ends mid-line starts a fresh line so the new title is read back
- Malformed (non-object) JSON in live-session, job and bridge files is skipped instead of crashing discovery
- `axism --version` shows a commit suffix only for a source checkout of aXism itself, never for an install that sits inside another git repository

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
