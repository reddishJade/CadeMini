# Persistence

Cade stores conversation history in a session log. Other stores have
separate responsibilities.

## Current structure

- Session logs use one closed schema without a format-version field. See
  [Session log schema](../reference/session-schema.md).
- Each entry is one JSON object followed by a newline.
- Sessions live under `.cade/sessions`.
- Compaction, windows, navigation, and recovery are entries, not history rewrites.
- File images live under `.cade/snapshots`; recovery actions and pending
  transactions live under `.cade/recovery`.
- Goals, NOTE, memory, and config remain separate artifacts.

Development changes can break persisted structures. Readers and writers support
only the current structure. There are no compatibility readers or migrations.
See [Change policy](change-policy.md).

## Verification

`test_log.py`, `test_session_history.py`, `test_projection.py`, and
`test_recovery.py` verify validation, projection, copies, and interrupted
recovery. Invalid input must fail before changing durable state.

See the [Persistence catalog](../design/persistence-catalog.md) for ownership and
locations.
