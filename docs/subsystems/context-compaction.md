# Context and compaction

## Request context

The system message is the base prompt plus request-only context:

1. Project instructions from `AGENTS.md`, `CLAUDE.md`, or variants.
2. Skill catalog names and descriptions.
3. `NOTE.md` working state.
4. `MEMORY.md` durable facts.
5. An active goal.

The request audit records this context and the normalized request; it does not
turn the text into conversation facts.

`/context` summarizes the current request snapshot by its actual sources:
system prompt, project instructions, working note, memory, goal, skill catalog,
loaded skills, built-in tool schemas, MCP schemas, conversation, tool results,
and retained summaries. Optional categories appear when present. Delegation
tool schemas count as built-in tools; child conversations are not parent context.
Each token belongs to one category. Input remaining excludes the output reserve
and headroom.

File sources include loaded `AGENTS.md` or its fallback, notes, memory, skill
bodies and references, read files, and attachments. MCP sources list current
schema counts and token estimates per server, plus resources, prompts, and tool
results still present in the active conversation. A schema refresh changes the
schema list; loaded content remains until projection removes it.

For OpenAI Responses, `/context` calls the
[input token count endpoint](https://developers.openai.com/api/docs/guides/token-counting)
with the same converted input, instructions, tools, and response format used by
inference. The returned total is displayed as **Input (provider count)**. This
does not generate a reply or add a conversation event. Endpoint or account
errors are shown alongside the explicitly labeled fallback estimate.

For `deepseek-flash` and `deepseek-v4-pro`, the command uses the
[official V4 tokenizer](https://api-docs.deepseek.com/quick_start/token_usage/)
through `tokenizers`. On first use, it downloads the public model archive and
caches the checksum-verified JSON under `~/.cade/tokenizers/`. Later inspections
work from that cache. Text uses model tokenization; message framing, images,
and server-side tool formatting still require an estimate. The report names
the tokenizer and does not label this total as a provider measurement.

Source breakdowns are estimates, not provider-supplied per-file billing data.
They use the selected model tokenizer when available, otherwise the character
heuristic. Input remaining uses the provider total when available. The latest
request's reported input/output usage remains separate from both current input
and cumulative run totals. Inspection does not contact MCP servers or modify
the conversation. Request admission retains its existing offline estimate;
normal model calls do not issue an additional pre-count request.

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
It retrieves evidence outside the current compaction or window boundary.
Exact content is capped; search results point to entry IDs.

## Request admission

Admission preserves the full projection. Overflow requires a logged context
transition or fails with `context_error`. See
[Projection and request](projection-request.md).

## Tests

`test_compaction.py`, `test_window.py`, `test_history.py`, and
the request tests cover cuts, summaries, fallback, and retrieval.
