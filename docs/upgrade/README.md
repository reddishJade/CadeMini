# Upgrade guide

This page records changes that require action from an existing user or
automation.

## Standalone project

Cade Mini now installs as `cade-mini`, with Python imports under `cade_mini`.
The old `cade` command and `cade.mini` namespace are removed. No compatibility
or migration tools are provided.

Authenticate again with `cade-mini login`. Credentials and skills use
`~/.cade-mini/`; project skills use `.cade-mini/skills/`. Mini no longer
reads `.cade` directories. Session schema v1 remains the current format.

## When an upgrade is needed

An upgrade entry is required when:

- the session schema version changes;
- a config key is renamed or removed;
- a CLI flag changes meaning;
- a tool name or parameter becomes incompatible;
- a persisted host-state file changes shape.

## Procedure for a future upgrade

1. Read the relevant decision record.
2. Back up `.cade-mini/` before migrating.
3. Run the documented migration command once.
4. Open a session and verify the projection and `history` tool.
5. Keep the old directory until the new version has passed a real task.

## Compatibility promise

Additive optional config fields are not breaking. A new event type is breaking
because the schema is closed. A removed tool or a changed parameter is breaking.
The change policy is in [Persistence](../persistence/change-policy.md).
