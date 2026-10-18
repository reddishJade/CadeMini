# Persistence

Cade Mini has one durable conversation format: the session log. The rest are
host-state files with narrow scopes.

## Current format

- Session log: write closed schema v2; read v1 and v2. See [Session log schema](../reference/session-schema.md).
- Entry file: one JSON object per line; a trailing newline is required.
- Session directory: `.cade-mini/sessions`.
- Compaction and windows are entries, not file rewrites.
- Snapshots, goals, NOTE, memory, and config are separate artifacts.

## Compatibility

A reader must reject an unknown schema version rather than guess. A writer must
not add fields that change the meaning of existing fields. Additive diagnostic
fields are allowed only when old readers can ignore them.

## History

V2 adds normalized request audits without rewriting v1 facts. Resume and rollback
are described in the [upgrade guide](../upgrade/README.md).

## Verification

`test_mini_log.py`, `test_mini_projection.py`, and
`test_mini_acceptance.py` exercise the current format. A schema change must add a
test that opens an old fixture and one that rejects a future version.

## Related

- [Persistence catalog](../design/persistence-catalog.md)
- [Change policy](change-policy.md)
- [Session log schema](../reference/session-schema.md)
