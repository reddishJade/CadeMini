# Snapshots and goals

## File snapshots

Cade Mini records a snapshot before a file tool changes a file. Snapshots live
under `.cade-mini/snapshots` with a JSONL index and one directory per snapshot.

Covered changes:

- `write` and `edit`.
- Common single-command shell mutations: `rm`, `mv`, `cp`, `touch`, `mkdir`,
  redirection with `>`, and `sed -i`.
- Creating a file records that it did not exist; undo removes it.

Not covered:

- Complex pipelines, command lists, unknown scripts, and commands that mutate
  files without naming them.
- Files larger than the snapshot limit (5 MiB) are rejected rather than
  silently skipped.
- Directory trees are not copied; directory mutations are skipped.

## Undo

```bash
cade-mini --undo
```

This restores the latest snapshot and exits without contacting a provider. In
the REPL, use `/undo`. Each call restores one snapshot, so repeated undo walks
back through the recorded changes.

Snapshots are project state, not session facts. They are not part of the
JSONL session branch.

## Goals

A goal is one durable objective stored in `.cade-mini/goal.json`. It has an
objective, a status (`active` or `done`), and an update time.

```bash
cade-mini --goal "Add a regression test for the parser" "Start the task."
```

In the REPL:

- `/goal` shows the current goal.
- `/goal TEXT` sets an active goal.
- `/goal done` marks it complete.
- `/goal clear` removes it.

The `goal` tool provides the same actions to the model. An active goal is added
to request context so a resumed or compacted session still knows the objective.
Goals are host state; they do not change the session schema.

Goals are intentionally small. Use `NOTE.md` for working details and `MEMORY.md`
for facts that outlive the objective.
