# Context and compaction

## Request context

The system message is the base prompt plus request-only context:

1. Project instructions from `AGENTS.md`, `CLAUDE.md`, or variants.
2. Skill catalog names and descriptions.
3. `NOTE.md` working state.
4. `MEMORY.md` durable facts.
5. An active goal.

The request audit records this context's digests; it does not turn the text into
conversation messages.

## Compaction

`Compactor` runs when the full projection exceeds the input budget.

1. Select the cut at a user boundary that keeps the recent tail under the
   configured target.
2. Serialize older messages, including a prior summary.
3. Ask the provider for a summary with a bounded output.
4. Append a `compaction` fact with summary and `first_kept_id`.
5. Reproject.

A summary failure or a cut with no messages appends an `attempt` fact and leaves
the conversation unchanged. A prior summary is included as `PRIOR SUMMARY`, so
compaction is iterative.

## Window fallback

If compaction cannot fit, the loop may call the window hook. The CLI starts a
window at the latest user entry with a fixed handoff that points to `NOTE.md`
and history. If the request still does not fit, the run terminates with
`context_error`.

## History

The `history` tool searches the branch or reads an exact entry or tool result.
It is the recovery path for evidence replaced during pruning or compaction.
Exact content is capped; search results point to entry IDs.

## Request pruning

Admission replaces old tool bodies with references before it drops turns. The
history tool remains the only exact source. See
[Projection and request](projection-request.md).

## Tests

`test_mini_compaction.py`, `test_mini_window.py`, `test_mini_history.py`, and
the request tests cover cuts, summaries, fallback, and retrieval.
