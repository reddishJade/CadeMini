# Change policy

Cade Mini is under active development. Persisted data has one current, closed
structure with no format-version fields. Breaking changes are allowed. Do not
add compatibility readers, migration commands, or special handling for earlier
formats.

## Rules

- Update producers, readers, tests, and field-level documentation together.
- Validate records before modifying durable state.
- Keep conversation history append-only during normal operation.
- Never infer missing request contents from current provider behavior.
- Keep audit facts out of conversation projection.
- Document each new field's owner and lifetime.
- Use a decision record for changes to durable behavior or ownership.

Verify current behavior and failure recovery with focused tests. Generated
catalogs and documentation must match the implementation. See
[Session log schema](../reference/session-schema.md) and
[Persistence](README.md).
