# Cade Mini session schema

> Current writer: v2. Readers accept v1 and v2, including mixed-version logs.
> This is the persistent format and projection contract.

## Envelope

One JSON object per line in `session-<id>.jsonl`:

```json
{"v":2,"id":"e2","parent":"e1","ts":"2026-10-18T03:00:00+00:00","type":"user","data":{"text":"Fix the test","source":"user"}}
```

| Field | Type | Contract |
| --- | --- | --- |
| `v` | `1` or `2` | Read both versions; append v2; reject other values |
| `id` | string | Unique in the file; 12 lowercase hex characters |
| `parent` | string or null | Previous head; null only for the first event |
| `ts` | string | ISO 8601 UTC timestamp |
| `type` | string | One of the event types below |
| `data` | object | Type-specific payload |

The first increment has one active branch: the head is the last event. `parent`
is stored from day one so fork and rewind do not need a schema change.

Unknown event types are a version error. Do not ignore them.

V2 payloads are validated on append and read. Unknown fields, wrong primitive
types, invalid enums, and non-finite numbers are rejected. Missing optional
fields are allowed but defaults are not written into the record. `user.source`
defaults to `user`; assistant model/provider/usage/replay metadata and nullable
artifact/handoff/usage/details fields may be absent. Cross-event call pairing
and context-cut references are validated by projection. V1 keeps legacy reading
rules. Branch copies preserve source event versions.

## Event types

### Conversation events

These produce model-visible messages.

| Type | Payload | Projection |
| --- | --- | --- |
| `system` | `{"text": str}` | Latest one is emitted first; never shadowed |
| `user` | `{"text": str, "source": "user"\|"runtime"}` | User message at its position |
| `assistant` | `{"blocks": [Block], "model": str, "provider": str, "usage": object\|null, "provider_state": object\|null}` | Assistant message; empty blocks are skipped |
| `tool_result` | `{"call_id": str, "status": "ok"\|"error"\|"interrupted", "content": str, "artifact": object\|null}` | Tool message; paired with its call |
| `compaction` | `{"summary": str, "first_kept_id": str, "tokens_before": int, "model": str, "provider": str, "usage": object\|null, "details": object\|null}` | Emits a summary message, then the tail from `first_kept_id` |
| `window` | `{"start_id": str, "reason": "manual"\|"fallback", "handoff": str\|null}` | Starts the projection at `start_id`; optional handoff summary |

A block is one of:

```json
{"type":"text","text":"..."}
{"type":"tool_call","id":"c1","name":"bash","args":{"command":"pytest -q"}}
```

`assistant.blocks` is the only record of a tool call. Do not add a separate
`tool_use` event. The `tool_result.call_id` pairs with it.

A `provider_state` is opaque adapter data for Responses replay. It is
stored on the assistant fact and passed back to the same adapter. It never enters
the conversation vocabulary.

### Log-only events

These are recorded but never projected.

| Type | Payload | Purpose |
| --- | --- | --- |
| `meta` | `{"session": str, "project": str, "created": str}` | First event; identifies the session |
| `request` | `{"model": str, "provider": str, "tools_digest": str, "conversation_digest": str, "context": [str], "estimated_tokens": int, "dropped": [str], "snapshot": object}` | Request audit; v2 adds the normalized snapshot below |
| `run` | `{"termination": str, "steps": int, "usage": object\|null, "error": str\|null}` | Run outcome |
| `attempt` | `{"model": str, "provider": str, "error": str, "usage": object\|null}` | Failed model attempt that produced no message |

The v2 `request.snapshot` contains `messages`, `tools`, `options`, `budget`, and
`report`. `budget` records the context window, output reserve, and headroom;
`report` records the admission result. Options omit API keys, all headers,
cancellation signals, and callbacks. This is the provider-interface input, not
an HTTP capture. The remaining request data is not redacted. A v1 request has no
snapshot; do not infer it from current provider behavior.

A failed attempt does not become an assistant message. A run result does not
duplicate the final answer; the answer is the last assistant text.

## Invariants

1. **One fact, one record.** A tool call appears once, in its assistant event.
   The final answer is not stored again.
2. **No derived content.** No replacement message list, no rendered preview used
   as history, no display summary.
3. **Decisions are facts.** A compaction cut and a window start are logged; the
   messages they produce are recomputed.
4. **Request context is audited, not history.** Dynamic context and the request
   envelope live in `request`; only conversation events project.
5. **A compaction cut is a user turn.** `first_kept_id` must name a `user` event
   before the compaction. A tool call and its result never cross the cut.
6. **A window start is a user turn.** `start_id` must name a `user` event before
   the window.
7. **The latest system event wins.** It is projected first and is not shadowed by
   compaction or a window.

## Projection

`project(branch) -> list[Message]` is pure and deterministic.

1. Find the latest `system` event. Emit it first.
2. Find the latest `window`. If present, start at its `start_id` and keep its
   non-empty `handoff` for a summary message.
3. Find the latest `compaction` at or after the window start. If present, emit
   its summary and start the tail at `first_kept_id`.
4. Walk the retained entries in order. Emit `user`, `assistant`, and
   `tool_result` messages. Skip `system`, `meta`, `window`, `compaction`,
   `request`, `run`, and `attempt`.
5. Pair tool calls with results by call ID.
6. For an open call at the end, synthesize one interrupted result. Do not write
   it to the log. Insert it after the assistant message that contains the call.
7. Reject an orphan result, a duplicate result, a duplicate call, a bad cut, or a
   bad window with `ProjectionError`.

Order of emitted summary messages: system, window handoff, compaction summary,
retained tail.

The wire adapter renders `SummaryMessage` as a user-role message wrapped in
`<compaction-summary>` or `<window-handoff>`. The internal role stays distinct so
hosts can render it differently.

## Storage

- Directory: `.cade-mini/sessions/` by default.
- File: `session-<id>.jsonl`.
- New interactive sessions defer file creation until the first user or assistant
  message. Setup events stay in memory and are flushed with that message.
  A cancelled first request still keeps its user message on disk.
- Append lock: `session-<id>.lock`.
- Run lock: `session-<id>.run.lock`; concurrent turns are rejected.
- Append: one line, `flush`, `fsync`.
- Before append: truncate an incomplete trailing line from a crash.
- On read: ignore an incomplete trailing line; reject any earlier invalid line.
- Reject duplicate IDs and forward parent references.
- No rewrite, no in-place edit.

## Compaction record

```json
{"v":2,"id":"e80","parent":"e79","ts":"...","type":"compaction",
 "data":{"summary":"## Goal\n...","first_kept_id":"e52","tokens_before":48210,
         "model":"gpt-5.6-luna","provider":"chatgpt",
         "usage":{"input":43000,"output":1200},"details":null}}
```

The summary is model-generated content and must be logged. The original events
before `first_kept_id` stay in the log. A later compaction starts after the
previous one and may include the previous summary in its input.

## Window record

```json
{"v":2,"id":"e90","parent":"e89","ts":"...","type":"window",
 "data":{"start_id":"e88","reason":"manual","handoff":"Optional note."}}
```

A window is the hard fallback. Compaction is the default. A window is explicit
through `/new-context`, or automatic when compaction cannot reduce enough.

## Versioning

`v` is the only compatibility switch. A change to an existing payload, a new
conversation event type, or a change to projection semantics requires a new `v`
and a migration note. Adding a log-only event type is a new `v` as well, because
older readers must reject it rather than ignore it. See the
[upgrade guide](../upgrade/README.md) for v1 resume and rollback.
