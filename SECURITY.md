# Security

aXism is a **local** user tool. It can list files under an agent config root, delete session fragments, move sessions between project dirs, and signal processes that appear in the live registry.

## Trust boundary

- Runs with your user privileges against `$CLAUDE_CONFIG_DIR` / `~/.claude` (or `--config-dir`).
- Does not send telemetry or upload transcripts.
- Path operations must stay under the configured projects root (and known fragment roots). Refuse path escape.

## Protected paths

Session delete must not remove: credentials, settings, skills, plugins, daemon keys, or project `memory/`.

## Reporting

Report security issues privately to the maintainers. Do not open public issues for exploitable path-escape or privilege concerns.
