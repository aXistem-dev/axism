# Changelog

All notable changes to aXism are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Publish tagged releases to PyPI via GitHub Actions Trusted Publisher (OIDC)
- Project homepage / repository / issues URLs in package metadata

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
