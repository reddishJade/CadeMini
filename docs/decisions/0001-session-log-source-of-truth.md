# 0001: Session log is the source of truth

Status: accepted

## Context

Coding agents commonly rewrite the message list during compaction or context
replacement. When the rewrite is the only copy, later code cannot tell what the
model saw, what was summarized, or what was pruned. Debugging and recovery
become guesswork.

## Decision

The append-only session log is the single source of truth. A deterministic
projection produces the model-visible conversation. Request construction only
admits the current request; it does not write facts.

Only a run appends assistant and tool-result facts. Compaction and window
changes are recorded as their own facts (`compaction`, `window`) while the
original messages remain in the log. `request`, `run`, and `attempt` are audit
facts and never project into the conversation.

## Consequences

- Replaying a branch is deterministic.
- Compaction is reversible by changing the projection, not by rewriting
  history.
- The history tool can retrieve evidence that the current request pruned.
- The log grows without bound; compaction controls request size, not disk size.
- Schema changes require a versioned decision because stored facts are durable.

The invariant is documented in [Architecture](../architecture.md), [Sessions and
context](../sessions-context.md), and the [schema](../reference/session-schema.md).
