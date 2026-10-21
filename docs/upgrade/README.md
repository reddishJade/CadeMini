# Upgrade guide

Cade is under active development. Breaking changes are allowed. Persisted
records have one current structure, with no compatibility or migration layer.
See [Change policy](../persistence/change-policy.md).

## Rename from Cade Mini

The distribution, Python package, command, configuration file, and state
directories are now `cade`. The tool does not read the old paths. To keep
existing data, move it before the first run:

```bash
mv ~/.cade-mini ~/.cade
mv .cade-mini .cade
mv cade-mini.json cade.json
```

Repeat the project moves in each workspace that used the old name. Sessions,
snapshots, goals, skills, and credentials keep working after the move.

## Sign in with ChatGPT

Use `chatgpt` for ChatGPT plan usage and `cade login` for Sign in with
ChatGPT. For API-key authentication, use `openai` or `deepseek`. Credentials live
under `~/.cade/`. See [CLI authentication](../cli.md#login).

## Session and workspace recovery

`/tree` preserves conversation branches. `/undo` and `/redo` reverse or reapply
one action. `/rewind` selects a checkpoint and restoration scope; idle double
Esc opens its selector. `/revert` restores selected tracked file changes without
trimming conversation.

Checkpoint ownership and action history belong to their original session.
Copied conversations do not inherit a workspace undo stack. The file-only
`--undo` operation remains separate. See
[Sessions and context](../sessions-context.md) and
[Recovery coverage](../snapshots-goals.md#checkpoint-recovery).

## Explicit context reduction

The loop tries compaction, then a logged window fallback. If the request remains
over budget, it returns `context_error`. It does not silently trim request-only
history. See [Sessions and context](../sessions-context.md#request-admission).

## Development changes

Update callers, tests, and documentation with each breaking change. Do not
invent missing historical data or rewrite sessions during normal operation.
Pending workspace recovery must complete before new agent writes are allowed;
see the recovery guide for interruption handling.
