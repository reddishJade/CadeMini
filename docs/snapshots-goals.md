# Snapshots and goals

## File snapshots

Cade Mini records a snapshot before a file tool changes a file. Snapshots live
under `.cade-mini/snapshots` with a JSONL index and one directory per snapshot.

Covered changes:

- `write` and `edit`.
- Simple literal-file shell mutations: `rm`, file-to-file `mv`/`cp`, `touch`,
  `echo` redirection, and `sed -i`. Quoted filenames can contain spaces.
  Directory operands, expansions, and unknown command forms are not captured.
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

This legacy CLI operation restores one file snapshot without contacting a
provider. It does not rewind conversation and is not an alias for TUI `/undo`.
It refuses concurrent workspace writes or a pending coordinated recovery. Stop
external file writers before any restoration.

Undo checks that the current file matches the recorded post-change state. It
rejects conflicts, corrupt payloads, and snapshots whose outcome is unknown.
A rejected undo leaves the file and index unchanged. Legacy snapshots have no
post-change state and require manual inspection; automatic undo rejects them.

Snapshots are project state, not session facts. They are not part of the
JSONL session branch.

## Checkpoint recovery

Each root user turn creates a checkpoint before execution. Registered file
mutations retain before/after content, existence, and permissions. Synchronous
subagents share the parent capture scope. Queued inputs get checkpoints when
execution starts; steering remains part of the active turn.

- `/undo` reverses one turn or explicit recovery action; `/redo` reapplies one.
  Neither reruns tools or contacts a provider. History survives restart.
- `/rewind` selects an active-path user prompt, then conversation, code, or both,
  followed by a preview and confirmation. It restores the point before the prompt.
- `/revert` selects a turn range and all or one tracked file. Explicit path
  arguments select multiple files. Conversation stays unchanged.

A new action discards the active redo chain. Old conversation branches remain.
Tree navigation never restores files; a stale undo/redo cannot cross a changed
conversation position. Rewind can still use owned checkpoints and computes code
changes from actual actions, including intervening undo/redo/revert operations.
Copied sessions do not inherit workspace actions. Historical prompts without an
owned checkpoint support conversation-only rewind.

Recovery checks all selected files and content blobs before modifying a file.
A changed target, unknown outcome, corrupt blob, symlink, hard link, or protected
metadata path blocks automatic code restoration. Restoring an older range also
fails if later edits conflict; there is no automatic reverse-patch merge.
File limits apply to both images. A missing post-change image makes that turn's
code checkpoint incomplete rather than pretending that recovery is available.
Unselected files and the user's Git index remain untouched. Directory trees,
untracked shell/hook changes, and external side effects are not restored.

Cooperating operations share `.cade-mini/recovery/workspace.lock`. The action
journal and checkpoint links use closed v1 records under that directory. A
`pending.json` plan stores both images before restoration begins. Files use
atomic per-file replacement; a multi-file restore is not a filesystem-wide
atomic operation. On interruption, resume the named session to finish the plan
idempotently. A third, externally edited state stops recovery without overwriting
that edit. Resolve the reported conflict and resume; do not delete the pending
plan to bypass the write guard. New agent writes remain blocked until it finishes.
External editors do not honor the lock and must be stopped during recovery.
Read-only mode blocks code recovery; conversation-only rewind remains available.
Esc cancels a preview, not a committing file restore. A normal TUI exit waits
for the recovery commit; a forced process interruption uses the pending plan.

Code-only restoration keeps model-visible conversation messages. The next
request receives an audited notice to inspect restored files rather than trust
older tool effects. Settings, credentials, grants, and external services do not
rewind with conversation history.

## Goals

A goal is one durable objective stored in `.cade-mini/goal.json`. It has an
objective, a status (`active` or `done`), and an update time.

```bash
cade-mini --goal "Add a regression test for the parser" "Start the task."
```

In the TUI:

- `/goal` shows the current goal.
- `/goal TEXT` sets an active goal.
- `/goal done` marks it complete.
- `/goal clear` removes it.

The `goal` tool provides the same actions to the model. An active goal is added
to request context so a resumed or compacted session still knows the objective.
Goals are host state; they do not change the session schema.

Goals are intentionally small. Use `NOTE.md` for working details and `MEMORY.md`
for facts that outlive the objective.
