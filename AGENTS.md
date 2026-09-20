# Agent instructions for aXism

**aXism** (*Agent Interactive Session Manager*) is a TUI + CLI session manager. Claude Code is the first provider; keep the product name and binary **`axism`** agent-agnostic.

## Stack

- Python 3.12+, Textual, uv, hatchling
- Binary: `axism`
- Package: `axism` under `src/axism/`
- Display name: **aXism**

## Hard rules

1. **Anonymity** — no hostnames, forge URLs, personal home paths, or secrets in tracked files. Fixtures use synthetic `/home/alice/...` or `/Users/alice/...` only. Run `python3 scripts/check_anonymity.py`.
2. **Protected paths** — never delete credentials, settings, skills, plugins, daemon keys, or project `memory/` with a normal session delete.
3. **Subprocess** — argv lists only; never `shell=True`.
4. **Stop** — `s`/`S` or `axism stop` runs ``claude stop`` (same as `/stop` while attached to a background session). Interactive sessions are not stoppable this way.
5. **Pane-aware keys** — Space / a / d / o / s / m follow the projects vs sessions matrix; **c/C clears all marks** (projects + sessions). **d/D** deletes projects when the projects pane is focused, sessions otherwise. **m/M moves** project(s) or session(s) to another path (pick existing or type cwd). **`,`** opens Settings (provider + config dir).
6. User-facing branding says **aXism**. Mention “Claude Code” only where the code shells out to `claude`.
7. **Docs are current-state only** — README and similar docs describe how the project works *now*, not how it evolved. Prefer editing content in place over phrases like “was removed,” “previously,” “used to,” “as of [date],” or “no longer.” Do not put agent/process meta-commentary into project files. **Exception:** keep `CHANGELOG.md` up to date on every version bump (see Versioning).
8. **Changelog** — when bumping the version, move `[Unreleased]` notes into a new `## [X.Y.Z] — YYYY-MM-DD` section in `CHANGELOG.md` and leave a fresh empty `[Unreleased]` stub.

## Layout

```text
src/axism/
  cli.py, tui/, agents.py, delete.py, discover.py, fragments.py, live.py, move.py,
  paths.py, rename.py, settings.py
  providers/          # SessionProvider protocol + registry; claude_code.py first backend
tests/                # synthetic alice fixtures
scripts/install.sh
scripts/check_anonymity.py
```

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
