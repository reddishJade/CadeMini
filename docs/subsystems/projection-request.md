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
3. Preserves all projected messages and tool bodies.
4. Converts to provider messages and tool definitions.
5. Returns a `Request` and a `BuildReport`.

`BuildReport` records estimated tokens and budget. Legacy dropped-call and
turn counters remain empty/zero. `require_sendable()` raises `RequestTooLarge`
when the full request cannot fit. The loop reduces context only through logged
compaction and window decisions, then rejects any remaining overflow.

## Context order

The system message carries base instructions plus, in order: instructions,
skill catalog, NOTE, memory, and goal. These inputs are request-only; the
The request audit records their full text, the admitted messages, tool definitions,
non-authentication options, and budget. Session files can contain sensitive input.

## Tests

`test_mini_projection.py`, `test_mini_request.py`, and
`test_mini_compaction.py` cover cuts, strict admission, and wire-format contracts.
