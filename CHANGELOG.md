# Changelog

All notable changes to aXism are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Cursor provider: IDE agent transcripts (`~/.cursor/projects/<slug>/agent-transcripts/`) and CLI chats (`~/.cursor/chats/<md5-cwd>/<id>/`, both the SQLite and JSON-sidecar shapes), with open, stop, rename, move, and delete
- Hermes provider: interactive sessions from `~/.hermes/state.db`, opened with `hermes --resume` and mutated through `hermes sessions`
- `--provider` flag and `axism providers` command
- Provider `Capabilities`, so keys a backend cannot perform report instead of failing
- Publish tagged releases to PyPI via GitHub Actions Trusted Publisher (OIDC)
- Project homepage / repository / issues URLs in package metadata

### Changed

- `SessionProvider` covers discovery, liveness, open/stop, fragments, rename, move, and delete; the TUI and CLI no longer reach into Claude Code's modules directly
- `--config-dir` overrides the active provider's root instead of always setting `$CLAUDE_CONFIG_DIR`
- Credentials, agent config files, and backend databases are protected for every provider

### Fixed

- CI pytest collection (`pythonpath` includes project root so `tests.*` imports resolve)

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
