# Security

aXism is a **local** user tool. It can list files under an agent config root, delete session fragments, move sessions between project dirs, copy sessions into another agent, and stop running agent sessions.

## Trust boundary

- Runs with your user privileges against each enabled agent's config root: `$CLAUDE_CONFIG_DIR` / `~/.claude`, `$CURSOR_DATA_DIR` / `~/.cursor`, `$HERMES_HOME` / `~/.hermes`, or a folder set in Settings or with `--config-dir`.
- Does not send telemetry or upload transcripts.
- Path operations must stay under the configured projects root (and known fragment roots). Refuse path escape.
- Subprocesses run with argv lists only, never through a shell.

## Protected paths

Session delete must not remove, for any agent: credentials, agent config files, skills, plugins, daemon keys, project `memory/`, or a backend's whole database (such as Hermes `state.db`). The shared list lives in `fragments.PROTECTED_NAMES`.

## Reporting

Report security issues privately to the maintainers. Do not open public issues for exploitable path-escape or privilege concerns.
