# Sessions and context

A session is an append-only JSONL file under `.cade-mini/sessions` by default.
The file name is `session-<id>.jsonl`. The session title is derived from the
first user message; no extra event type is needed.

## Resume

```bash
cade-mini --list-sessions
cade-mini --continue "Continue where we stopped."
cade-mini --resume SESSION_ID "Explain the current state."
```

`--continue` and `--resume` open the latest or a specific session. Existing
sessions keep their original system fact; a new `cade-mini.json` does not rewrite
history. If a resumed session has no system entry, the current prompt is added.

## Tree, fork, and clone

| Command | Result |
| --- | --- |
| `/tree [ENTRY]` | Switch position within the same session, preserving all branches |
| `/fork [N\|ENTRY]` | Copy history before a selected user input into a new session |
| `/clone` | Copy the complete current active path into a new session |

`/tree` displays the full history tree. Selecting a user input restores its text
in the editor and continues from its parent. Selecting another entry continues
after that entry with an empty editor. Navigation and pending historical input
survive restart. The next model request contains only the selected path.
Idle double Esc with empty input opens the separate `/rewind` checkpoint selector.

`/fork` can select user inputs from inactive branches as well as the active one.
`/fork N` selects the Nth most recently recorded input; `/fork ENTRY` selects an
entry directly. Exact entry IDs take precedence over numeric turn selectors.
The selected input returns to the editor in the new session.
`/clone` clears the editor and copies only the active path, not other branches.
Copies receive new entry IDs, preserve timestamps and source record versions,
and remap compaction/window references. Navigation and checkpoint controls are
omitted. Copies do not inherit the source's workspace undo stack.

All three operations leave workspace files unchanged. Fork and clone leave the
source session unchanged. They do not implement conversation/file undo and do
not generate an automatic summary of the abandoned branch. Run history changes
only when no task is active.

See the [session schema](reference/session-schema.md) and
[upgrade guide](upgrade/README.md#session-v4-and-workspace-recovery) before
opening existing sessions with the new writer.

## Checkpoint recovery

`/undo` and `/redo` reverse or reapply one action. A root user turn, including
synchronous subagent edits, is one action. Explicit rewind and revert are also
single reversible actions. New actions discard the active redo chain; original
conversation branches remain. Navigation outside the action chain prevents a
stale undo/redo from being applied; use `/rewind` to select the intended point.

`/rewind` restores the position before a selected active-path user prompt and
asks whether to restore conversation, tracked code, or both. `/revert` selects
only code changes over a turn range and file subset. Code-only restoration keeps
conversation messages and adds an audited request-context notice. It does not
rewind provider settings, permissions, credentials, or other host state.

Old or copied turns without owned checkpoints remain conversation-restorable
only. File coverage, conflicts, and interrupted transactions are described in
[Snapshots and goals](snapshots-goals.md#checkpoint-recovery).

## Compaction

When the full conversation exceeds the input budget, `Compactor`:

1. Selects a cut at a visible user boundary.
2. Sends the older messages to the provider for a summary.
3. Appends a `compaction` fact with the summary and `first_kept_id`.
4. Reprojects the conversation as the latest system, summary, and retained tail.

Compaction is iterative: a prior summary is serialized as `PRIOR SUMMARY` and
can be summarized again. The original user, assistant, and tool-result facts
remain in the log.

## Request admission

Request construction preserves the entire projection, including tool outputs.
Only logged compaction and window events reduce visible history. If the request
still exceeds the budget after these steps, the loop returns `context_error`
without sending it to the provider. Use the `history` tool to retrieve evidence
outside the current context.

## Windows

A `window` fact starts a new context at a user entry and may include a handoff.
The TUI can start one explicitly with `/new-context`. If compaction cannot bring
a request under budget, the loop can append an automatic window with a fixed
handoff that points to `NOTE.md` and the history tool.

## History tool

`history` accepts one of:

- `query`: case-insensitive search over the branch, returning entry IDs and
  snippets.
- `entry_id`: exact content of one log entry.
- `call_id`: exact tool result for one call.

Exact content is capped. Search results are capped and point back to entry IDs.

## Request context order

For each task the request contains:

1. The system message.
2. Project instructions from `AGENTS.md`, `CLAUDE.md`, or their variants.
3. The skill catalog.
4. `NOTE.md` working state.
5. `MEMORY.md` durable facts.
6. An active goal.

Items 2-6 are request-only context. They are recorded in the request audit but
never become conversation facts. See [Architecture](architecture.md) and the
[design](history/design-proposal.md).
