# Change policy

A persisted format change is a public interface change. It needs a decision,
tests, and a migration path.

## Before changing the schema

1. Write a decision record under `docs/decisions/`.
2. State whether the change is additive, transformative, or breaking.
3. Describe how an old session is read and whether it is rewritten.
4. Define the new schema version and the reader's rejection rule.
5. Add an old-format fixture and a migration test.

## Rules

- Never rewrite a user's session file in place without an explicit migration.
- Never infer a missing field from a provider's current behavior.
- Keep audit facts out of the projection unless the schema says otherwise.
- Bump the version for any change that an old reader cannot ignore.
- Update [Persistence](README.md), the [schema](../reference/session-schema.md), and
  the [upgrade guide](../upgrade/README.md) in the same change.

## Review checklist

- Old fixture opens and projects identically.
- Unknown future version fails with a clear error.
- Migration is idempotent and can be interrupted.
- New fields have a documented owner and lifetime.
- Session size and compaction behavior remain bounded.

## Current status

Writers use closed schema v4. Readers accept v1, v2, v3, and v4 without rewriting old
facts. The [upgrade guide](../upgrade/README.md) documents resume and rollback.
