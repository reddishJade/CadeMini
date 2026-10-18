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

## Fork, clone, tree, and rewind

- `/clone` copies the active branch into a new session.
- `/fork [N|ENTRY]` copies history before a user turn (default: latest) into a
  new session and prints the forked input.
- `/tree` lists entries; `*` marks the active branch.
- `/rewind [N]` creates a new session without the latest N user turns.
- `/undo [N]` restores the latest N file snapshots.

These operations create new sessions; they do not rewrite the original file.
Compaction and window references are remapped when entries are copied.

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
The CLI can start one explicitly with `/new-context`. If compaction cannot bring
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
