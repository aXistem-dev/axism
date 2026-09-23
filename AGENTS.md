# Agent instructions for aXism

**aXism** (*Agent Interactive Session Manager*) is a TUI + CLI session manager for Claude Code, Cursor, and Hermes. Keep the product name and binary **`axism`** agent-agnostic.

## Stack

- Python 3.12+, Textual, uv, hatchling
- Binary: `axism`
- Package: `axism` under `src/axism/`
- Display name: **aXism**

## Hard rules

1. **Anonymity** — no hostnames, forge URLs, personal home paths, or secrets in tracked files. Fixtures use synthetic `/home/alice/...` or `/Users/alice/...` only. Run `python3 scripts/check_anonymity.py`.
2. **Protected paths** — never delete credentials, agent config, skills, plugins, daemon keys, project `memory/`, or a backend's whole database with a normal session delete. `fragments.PROTECTED_NAMES` is shared by every provider.
3. **Subprocess** — argv lists only; never `shell=True`.
4. **Go through the provider** — TUI and CLI call `SessionProvider` methods only. Never import `discover` / `live` / `rename` / `move` / `delete` / `fragments` directly from `cli.py` or `tui/app.py`; those are Claude Code's implementation, wrapped by `ClaudeCodeProvider`.
5. **Unsupported means explicit** — declare it in `Capabilities`, then raise `UnsupportedOperation` (or return a failed `MoveResult` / `(False, message)`). Never silently no-op.
6. **Stop** — `s`/`S` targets whatever `provider.can_stop(live)` allows: Claude Code background jobs via ``claude stop``, otherwise the agent process. Never map Stop onto a global switch such as `hermes pause`.
7. **Pane-aware keys** — Space / a / d / o / s / m follow the projects vs sessions matrix; **c/C clears all marks** (projects + sessions). **d/D** deletes projects when the projects pane is focused, sessions otherwise. **m/M moves** project(s) or session(s) to another path (pick existing or type cwd) within the same agent tool. **`,`** opens Settings (include backends + config dirs). Enabled backends are federated into one inventory; marks/live use `provider:id` keys.
8. User-facing branding says **aXism**. Name a specific agent only where the code shells out to it.
9. **Docs are current-state only** — README and similar docs describe how the project works *now*, not how it evolved. Prefer editing content in place over phrases like “was removed,” “previously,” “used to,” “as of [date],” or “no longer.” Do not put agent/process meta-commentary into project files. **Exception:** keep `CHANGELOG.md` up to date on every version bump (see Versioning).
10. **Changelog** — when bumping the version, move `[Unreleased]` notes into a new `## [X.Y.Z] — YYYY-MM-DD` section in `CHANGELOG.md` and leave a fresh empty `[Unreleased]` stub.

## Layout

```text
src/axism/
  cli.py, tui/, agents.py, delete.py, discover.py, fragments.py, live.py, move.py,
  paths.py, rename.py, settings.py     # Claude Code implementation + shared DTOs
  providers/
    base.py           # SessionProvider protocol, Capabilities, ProviderBase
    common.py         # shared helpers for filesystem-backed backends
    __init__.py       # registry: PROVIDER_LABELS + get_provider
    claude_code.py    # wraps the top-level modules above
    cursor.py + cursor_store/     # ~/.cursor agent transcripts + CLI SQLite chats
    hermes.py + hermes_store/     # ~/.hermes state.db, mutations via the hermes CLI
tests/                # synthetic alice fixtures
scripts/install.sh
scripts/check_anonymity.py
```

## Provider scope

Each backend manages **default chat sessions only**. Work beyond that belongs on its own branch so the shared protocol stays stable:

| Branch | Scope |
|--------|-------|
| `feat/hermes-extras` | Hermes profiles (`profiles/<name>/state.db`), kanban boards (`kanban.db`), `hermes project` workspaces (`projects.db`), messaging/cron sources |
| `feat/cursor-extras` | Cursor desktop Composer history in the Electron profile (`state.vscdb`) |

If one of those needs a protocol change, extend `SessionProvider` in a way every backend can satisfy — never with a single-backend assumption in `base.py`.

## Adding a provider

1. Create `providers/<name>.py` subclassing `ProviderBase`, plus `providers/<name>_store/` for its layout. Do not import another backend's store package.
2. Map into the shared DTOs (`ProjectInfo`, `SessionMeta`, `LiveSession`, `Fragment`); use `SessionMeta.entrypoint` for a badge rather than new required fields.
3. Register the id and label in `providers/__init__.py`, and set `config_hint` for the Settings placeholder.
4. Prefer the agent's own CLI for mutations when one exists; fall back to filesystem operations only where it does not.
5. Add tests with synthetic fixtures under `tests/`; never touch a real agent home in a test.

## Dev commands

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
python3 scripts/check_anonymity.py
uv run axism --help
uv tool install --force .
```

## Versioning & release

Bump **all** of:

| File | Field / action |
|------|----------------|
| `pyproject.toml` | `project.version` |
| `src/axism/__init__.py` | `__version__` |
| `CHANGELOG.md` | move `[Unreleased]` → `## [X.Y.Z] — YYYY-MM-DD`; leave empty `[Unreleased]` |

Tag `v*` (e.g. `v0.1.0`) to trigger `.github/workflows/release.yml` (GitHub Release assets + PyPI). Keep docs forge-agnostic (no hardcoded private hosts).

## Commits

Prefer generic messages (“add pane-aware select”, not machine- or customer-specific notes).
