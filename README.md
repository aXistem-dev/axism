![aXism](https://raw.githubusercontent.com/aXistem-dev/axism/main/aXism-BANNER.png)

# aXism

**Agent Interactive Session Manager.** Browse, open, and clean up the chat sessions your coding agents keep on disk, across every project on your machine.

[![CI](https://img.shields.io/badge/CI-pytest%20%2B%20ruff-brightgreen.svg)](https://github.com/aXistem-dev/axism/blob/main/.github/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/axism.svg)](https://pypi.org/project/axism/)
[![GitHub Release](https://img.shields.io/github/v/release/aXistem-dev/axism)](https://github.com/aXistem-dev/axism/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aXistem-dev/axism/blob/main/LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/confituurke)

aXism is a terminal app plus a command-line tool. It finds every session from **Claude Code**, **Cursor**, and **Hermes**, groups them by project, and lets you open, stop, rename, move, or delete them without digging through hidden folders. It runs on Linux and macOS.

## Why use it

- **See everything in one place.** Every project and session, with title, size, last update, and whether it is running right now.
- **Clean up safely.** Deleting a session removes all of its files (transcripts, caches, job records) and nothing else. Credentials, settings, skills, plugins, and project memory are never touched. Every delete asks first.
- **Keep sessions with their project.** Renamed or moved a project folder? Move its sessions to the new path so your agent finds them again.
- **Jump back in.** Open any session in its own agent with one key.

## Install

Needs Python 3.12 or newer.

```bash
uv tool install axism
# or
pipx install axism
```

Make sure `~/.local/bin` is on your `PATH`, then check it works with `axism --version`.

To upgrade, run `uv tool upgrade axism` or `pipx upgrade axism`. If the version does not change, the tool was installed from a pinned file instead of PyPI; reinstall with `uv tool install --force axism`.

## First steps

```bash
axism            # open the app
axism list       # print every session, grouped by project
axism providers  # show which agents are enabled and where aXism looks for them
```

The app has three panes: **projects**, **sessions**, and **details**. Use `Tab` or the arrow keys to switch panes, `↑`/`↓` to pick an item, and press `?` to see every key.

## Using the app

| Key | Action |
|-----|--------|
| `Tab` / `←` `→` | Switch pane |
| `Space` / `a` / `c` | Mark one, mark all, clear all marks |
| `o` | Open the session in its agent |
| `s` | Stop a running session |
| `n` | Rename a session |
| `m` | Move to another project path |
| `d` | Delete |
| `/` / `.` | Filter / sort sessions |
| `,` | Settings |
| `?` or `h` | All keys (refresh, themes, version info, and more) |
| `q` | Quit |

Keys act on the pane you are in. For example, `d` in the projects pane deletes whole projects, while `d` in the sessions pane deletes sessions. If items are marked, the action applies to all of them. If an agent cannot do something, aXism tells you instead of doing half the job.

**Moving.** Press `m`, then pick an existing project or type a folder path. Within the same agent, aXism moves the session files and updates their recorded paths. It creates the destination folder if needed, but it never moves your source code. Stop running sessions before moving them. Moving a session into another agent's project copies the conversation text and keeps the original.

**Settings.** Press `,` to choose which agents to include (`Space`), set the default agent for CLI commands (`Enter` saves and makes the highlighted one the default), and point an agent at a custom config folder. Settings are saved in `~/.config/axism/settings.json` (or under `$AXISM_CONFIG_DIR`).

## Command line

Everything the app does is also available as a command, which is handy for scripts. Run `axism <command> --help` for all options.

| Command | What it does |
|---------|--------------|
| `list [--project PATH] [--json]` | List sessions |
| `show ID` | Show a session's details and files |
| `resume ID` | Open a session in its agent |
| `stop ID` | Stop a running session |
| `rename ID "Title"` | Rename a session |
| `move ID... --to PATH` | Move sessions (add `--project` to move whole projects) |
| `export ID --to-provider AGENT --to PATH` | Copy a session into another agent |
| `delete ID...` | Delete sessions |
| `delete-project PATH` | Delete a project and its sessions |
| `purge-project PATH` | Run Claude Code's own project purge |

A few things worth knowing:

- Session IDs can be shortened to any unique prefix.
- `delete`, `delete-project`, `purge-project`, and `stop` only show a preview until you add `--yes`. `move` and `export` take `--dry-run` to preview.
- `list` covers every enabled agent. Other commands use the default agent (Claude Code unless you change it), so add `--provider cursor` or `--provider hermes` to work on those sessions.

```bash
axism list --project /home/alice/src/demo
axism delete 3f2a9c1e --dry-run
axism delete 3f2a9c1e --yes
axism move --project /home/alice/src/demo --to /home/alice/src/demo-v2
axism --provider hermes show <session-id>
```

## Supported agents

| Agent | Id | Default location | Override with |
|-------|----|------------------|---------------|
| Claude Code | `claude_code` | `~/.claude` | `$CLAUDE_CONFIG_DIR` |
| Cursor | `cursor` | `~/.cursor` | `$CURSOR_DATA_DIR` |
| Hermes | `hermes` | `~/.hermes` | `$HERMES_HOME` |

You can also set a folder per agent in Settings, or pass `--config-dir` on the command line.

All three agents support open, stop, rename, and delete. Differences:

- **Stop** on Claude Code applies to background jobs (via `claude stop`). On Cursor and Hermes it ends the agent process.
- **Move** works for Claude Code and Cursor. Hermes sessions can be copied to another agent but not moved.
- **Project memory merge** is Claude Code only: moving a project into another one also merges its `memory/` folder.
- **Project purge** (`purge-project`) is Claude Code only.
- **Hermes** changes go through the `hermes` command, and only regular chat sessions are listed. Profiles, kanban boards, project workspaces, messaging, and cron sessions are left alone.
- **Cursor** desktop Composer history is not managed.

## What is never deleted

Deleting a session never removes credentials (`auth.json`, `.credentials.json`, `.env`), agent config (`settings.json`, `config.yaml`, `cli-config.json`), skills, plugins, daemon keys, project `memory/`, or an agent's whole database (such as Hermes `state.db`).

Deleting a project removes every session in it along with the agent's folder for that project. For Claude Code that includes the project's `memory/` unless you pass `--keep-memory`.

## Development

From a clone of this repository:

```bash
uv sync --extra dev
uv run pytest
./scripts/install.sh   # install your local checkout as the axism command
```

See [AGENTS.md](https://github.com/aXistem-dev/axism/blob/main/AGENTS.md) for contributor rules and the release process, and [CHANGELOG.md](https://github.com/aXistem-dev/axism/blob/main/CHANGELOG.md) for what changed in each version.

## License

[MIT](https://github.com/aXistem-dev/axism/blob/main/LICENSE) © aXism contributors
