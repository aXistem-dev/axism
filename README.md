# aXism

**Agent Interactive Session Manager** — list, inspect, open, stop, rename, move, and purge coding-agent chat sessions and their on-disk fragments across every project on your machine.

**Current version: `0.1.0`** (`pyproject.toml` / `axism --version`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](pyproject.toml)
[![CI](https://img.shields.io/badge/CI-pytest%20%2B%20ruff-brightgreen.svg)](.github/workflows/ci.yml)

<p align="center">
  <a href="https://buymeacoffee.com/confituurke">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50" width="217" />
  </a>
</p>

Works on **Linux** and **macOS**, across three agent backends:

| Provider | Id | Session store | Config override |
|----------|----|---------------|-----------------|
| [Claude Code](https://code.claude.com/) (default) | `claude_code` | `projects/<slug>/<uuid>.jsonl` | `$CLAUDE_CONFIG_DIR` or `~/.claude` |
| Cursor | `cursor` | `projects/<slug>/agent-transcripts/<id>/` and `chats/<md5-cwd>/<id>/store.db` | `$CURSOR_DATA_DIR` or `~/.cursor` |
| Hermes | `hermes` | `state.db` (`sessions` + `messages`) | `$HERMES_HOME` or `~/.hermes` |

Pick one with Settings (`,`) in the TUI or `--provider` on the CLI.

## Features

- Cross-project session inventory with sizes, tags, and live/background state
- Pane-aware Textual TUI: **projects (25%) · sessions (50%) · detail**
  - Detail shows **project** summary when the projects pane is focused; **session** detail otherwise
  - Footer only lists essentials (plus Settings / Help); `?` has the full key legend
  - Theme-aware markup (`t` picker / `T` cycle)
- Mark, open, stop, rename, **move**, and delete — all respect focus and multi-select
- **Delete** is pane-aware: `d` on projects deletes projects; `d` on sessions deletes sessions (project delete confirm uses a stronger danger style)
- Move projects or sessions to another project (pick existing or type a path)
- Project moves merge `memory/` into the destination (colliding names get a `-from-<slug>` suffix; dual `MEMORY.md` can hand off to a local agent CLI)
- Settings (`,`) for active provider and config directory (`~/.config/axism/settings.json`)
- Safe delete with confirm (stops live processes when deleting)
- Header shows brand with version/commit beside it; `v` for runtime details
- Sessions pane: `/` filter, `.` cycle sort (updated / title / size)
- Open sessions via the provider CLI (`claude attach` / `claude --resume`, `cursor-agent --resume`, `hermes --resume`)
- Keys that a backend cannot perform say so instead of failing halfway
- Headless CLI for scripting

## Install

```bash
# once published to PyPI
pipx install axism
# or
uv tool install axism
```

From GitHub release assets:

```bash
uv tool install https://github.com/aXistem-dev/axism/releases/download/v0.1.0/axism-0.1.0-py3-none-any.whl
```

From a clone (development):

```bash
./scripts/install.sh
# or: uv tool install --force .
```

Ensure `~/.local/bin` is on your `PATH`. Then:

```bash
axism --version   # e.g. axism 0.1.0
axism --help
```

## Quick start

```bash
axism          # TUI
axism tui
axism list
axism list --project /home/alice/src/demo
axism list --json
```

## Keyboard (TUI)

Actions are **pane-aware**. Most letter keys accept both cases (`a` / `A`, `q` / `Q`, …). The footer hides keys that cannot run right now (e.g. Open/Rename/Stop on the projects pane, Stop when nothing is stoppable). Press **`?`** / **`h`** for the full legend.

| Key | Projects pane | Sessions / detail |
|-----|---------------|-------------------|
| `Tab` / `←` / `→` | Switch pane | Switch pane |
| `↑` / `↓` | Move in list | Move in list |
| `Space` | Mark project | Mark session (no-op on detail) |
| `a` / `A` | Select all projects | Select all sessions in project |
| `c` / `C` | Clear **all** marks (projects + sessions) | Clear **all** marks |
| `o` / `O` | — | **Open** — live background → `claude attach`; else `claude --resume` |
| `s` / `S` | — | **Stop** background session(s) (same as `/stop` while attached) |
| `n` / `N` | — | **Rename** focused session |
| `m` / `M` | **Move** marked/focused project(s) | **Move** marked/focused session(s) |
| `/` | — | Focus session filter |
| `.` | — | Cycle session sort |
| `d` / `D` | Delete marked/focused project(s) | Delete marked/focused session(s) |
| `r` / `R` | Refresh | Refresh |
| `t` | Theme picker | Theme picker |
| `T` | Cycle theme | Cycle theme |
| `,` | Settings (provider + config dir) | Settings |
| `v` / `V` | Version details | Version details |
| `?` / `h` | Help | Help |
| `q` / `Q` | Quit | Quit |

**Move** opens a dialog: pick another project from the list (↑/↓), or **Tab** to the path field and type an absolute workspace path (**Shift+Tab** returns to the list). One project → a new path renames the Claude project directory and creates the workspace folder if needed (source tree files stay put); otherwise sessions and `memory/` are merged into the destination. Live/background sessions must be stopped first. If both sides have `MEMORY.md`, aXism offers a handoff to a detected agent CLI (`claude`, `cursor`, `opencode`, `aider`, `codex`, …) or saves a task brief only.

Confirm dialogs use `y` / `n` / `Esc`.

## CLI

```bash
axism --version
axism list
axism list --project /home/alice/src/demo
axism list --json
axism show <session-uuid>
axism resume <session-uuid>
axism resume <session-uuid> --print-only
axism stop <session-uuid> --yes
axism rename <session-uuid> "New title"
axism move <session-uuid> [<uuid>…] --to /home/alice/src/other
axism move --project /home/alice/src/demo --to /home/alice/src/other
axism move --project -home-alice-src-demo --to /home/alice/src/other --dry-run
axism move --project /home/alice/src/demo --to /home/alice/src/other --memory-agent auto
axism move --project /home/alice/src/demo --to /home/alice/src/other --memory-agent claude --memory-agent-force
axism move --project /home/alice/src/demo --to /home/alice/src/other --no-merge-memory
axism delete <session-uuid> --dry-run
axism delete <uuid-a> <uuid-b> --yes
axism delete-project /home/alice/src/demo --dry-run
axism delete-project -home-alice-src-demo --yes
axism delete-project -home-alice-src-demo --yes --keep-memory
axism purge-project /home/alice/src/demo --dry-run   # wraps: claude project purge
```

Global flags: `--provider {claude_code,cursor,hermes}`, `--config-dir`, `--no-cli` (skip agent-CLI live enrichment), `--version`.

```bash
axism providers                      # backends, enabled state, and resolved roots
axism --provider cursor list
axism --provider hermes show <session-id>
```

TUI **Settings** (`,`) stores the active provider and optional config dir in `~/.config/axism/settings.json` (or `$AXISM_CONFIG_DIR`) under `providers.<id>`. `--provider` overrides the saved choice for one command; `--config-dir` overrides that backend's root.

## Backend differences

Every backend implements the same provider interface, and each one refuses what its agent genuinely cannot do:

| Capability | Claude Code | Cursor | Hermes |
|------------|-------------|--------|--------|
| Open / resume | `claude attach` or `claude --resume` | `cursor-agent --resume` | `hermes --resume` |
| Stop | `claude stop` on background jobs | ends the `cursor-agent` process | ends the `hermes` process |
| Rename | `custom-title` transcript record | `store.db` name / title sidecar | `hermes sessions rename` |
| Move sessions / projects | yes, with `memory/` merge | yes (relocates chat dirs) | not supported |
| Delete session | fragment plan across the tree | removes the chat dir in each store | `hermes sessions delete` |
| Project purge | `claude project purge` | not supported | not supported |

Hermes reads `state.db` read-only and only lists interactive sources (`cli`, `tui`, `desktop`, `webui`) — cron and kanban worker sessions stay hidden.

A Cursor chat started from the CLI exists in both stores under one id; aXism shows it once and treats both directories as its fragments.

### Not managed

These stores are intentionally left alone:

- Hermes named profiles (`~/.hermes/profiles/<name>/`, each with its own `state.db`), kanban boards (`kanban.db`), and `hermes project` workspaces (`projects.db`)
- Hermes gateway/messaging sessions (Telegram, Discord, Slack, …) and cron runs
- Cursor's desktop Composer history in the Electron profile (`~/.config/Cursor` / `~/Library/Application Support/Cursor`)

## How it works

aXism scans the active **provider** config root, maps each session id to its transcripts, jobs, subagents, caches, and live records, then lets you open, stop, rename, move, or delete those fragments without touching protected global state. The TUI and CLI only ever call the provider interface in [`src/axism/providers/base.py`](src/axism/providers/base.py); each backend owns its own layout under `providers/<name>_store/`.

Claude Code's tree:

```text
configRoot
├── projects/<slug>/<uuid>.jsonl     transcripts
├── projects/<slug>/<uuid>/          subagents / tool-results
├── projects/<slug>/memory/          project memory (protected on session delete)
├── file-history/<uuid>/
├── jobs/<short8>/
├── sessions/<pid>.json              live registry
└── history.jsonl                    prompt index (rewritten on delete / move)
```

Project slugs are Claude Code’s encoding of an absolute cwd (non-alphanumeric → `-`).

**Move** relocates session files between `projects/<slug>/` dirs (or renames a whole project dir), rewrites **all** transcript `cwd` fields to the destination, updates `history.jsonl` / job `cwd` when present, creates the destination workspace folder if it does not exist (it does **not** move your source tree files), and leaves UUID-keyed fragment trees (`file-history`, …) in place. Project merges also merge `memory/` (unique files move; content collisions become `-from-<slug>` names; dual `MEMORY.md` parks the source index for an optional agent handoff).

## What is protected

Never deleted with a session: credentials (`auth.json`, `.credentials.json`, `.env`), agent config (`settings.json`, `config.yaml`, `cli-config.json`), skills, plugins, daemon keys, project `memory/`, the Hermes `state.db`, and unrelated global caches.

**Project delete** removes `projects/<slug>/` (memory unless `--keep-memory`) plus linked session fragments outside that directory.

## Build from source

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
python3 scripts/check_anonymity.py
uv build                          # dist/axism-0.1.0-*.whl and *.tar.gz
./scripts/install.sh              # reinstall tool + print axism --version
```

## Versioning

Bump **all** when releasing:

| File | Field |
|------|--------|
| `pyproject.toml` | `project.version` |
| `src/axism/__init__.py` | `__version__` |
| `CHANGELOG.md` | new `[X.Y.Z]` section from `[Unreleased]` |

```bash
axism --version          # installed binary
# → axism 0.1.0
```

Tag `v*` (e.g. `v0.1.0`) to trigger the release workflow.

## Releases & CI

- **CI** (`.github/workflows/ci.yml`): pytest, ruff, anonymity check — works on GitHub Actions and Forgejo/Gitea/Codeberg Actions.
- **Release** (`.github/workflows/release.yml`): on tag `v*`, builds wheel/sdist, uploads GitHub release assets, and publishes to PyPI via Trusted Publisher (OIDC).

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Contributing

See [AGENTS.md](AGENTS.md) for coding-agent and human contributor rules (anonymity, pane-aware keys, safe delete / move).

## Support

[![Buy Me A Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://buymeacoffee.com/confituurke)

## License

[MIT](LICENSE) © aXism contributors
