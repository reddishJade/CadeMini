# Projection and request

## Projection

`project(entries)` is pure. It:

1. Finds the latest `system` entry.
2. Applies the latest `window` start.
3. Applies the latest `compaction` cut and emits a summary message.
4. Converts user, assistant, and tool-result facts to messages.
5. Pairs each tool call with its result by call ID.
6. Inserts a synthetic interrupted result if the branch ends inside a call.

It rejects orphan results, duplicate results, and cuts that do not point to a
user entry. The projection does not call a provider or write the log.

## Request

`build(conversation, tools, budget, context)`:

1. Joins the base system prompt with request-only context.
2. Estimates tokens.
3. Replaces old tool bodies with references, keeping recent results first.
4. Drops the oldest user turns if still over budget.
5. Converts to provider messages and tool definitions.
6. Returns a `Request` and a `BuildReport`.

`BuildReport` records estimated tokens, budget, dropped call IDs, and dropped
turns. A request that cannot fit raises `RequestTooLarge`.

## Pruning rules

- The latest four tool results are protected in the first pass.
- If still over, protected results are pruned oldest first.
- The newest result is pruned only as a last resort.
- The replacement names the call ID and points to the history tool.

## Context order

The system message carries base instructions plus, in order: instructions,
skill catalog, NOTE, memory, and goal. These inputs are request-only; the
v2 request audit records their full text, the admitted messages, tool definitions,
non-authentication options, and budget. Session files can contain sensitive input.

## Tests

`test_mini_projection.py`, `test_mini_request.py`, and
`test_mini_compaction.py` cover the cut, pruning, and wire-format contracts.
