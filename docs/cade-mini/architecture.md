# Architecture

Cade Mini is a pipeline with four stages. Each stage has one owner and one
direction of data flow.

```text
Session log -> Conversation projection -> Request -> Run
```

## Session log

`SessionLog` is an append-only JSONL file. Each entry has an ID, timestamp,
type, parent ID, and data. The active branch ends at the latest entry. A tree is
possible, but the loop follows one branch.

The log stores facts:

- `system`, `user`, `assistant`, and `tool_result` are model-visible facts.
- `compaction` records a summary and `first_kept_id`.
- `window` records a new context start and an optional handoff.
- `request`, `run`, and `attempt` are audit facts. They never become
  conversation messages.

The schema is closed and versioned. See [Session log schema](../cade-mini-schema.md).

## Projection

`project(entries)` is a pure function. It reads the branch and produces the
deterministic conversation projection:

1. Take the latest system message.
2. Apply the latest window start.
3. Apply the latest compaction summary.
4. Keep retained user, assistant, and tool-result messages.
5. Pair tool calls with tool results; insert a synthetic interrupted result at
   the tail when the log ends mid-call.

No projection step writes to the log. Replaying the same entries always
produces the same conversation.

## Request

`build(conversation, tools, budget, context)` admits messages into the current
model window. It:

- joins request-only context such as instructions, skill catalog, NOTE, memory,
  and goal;
- replaces old tool bodies with references to the history tool;
- drops the oldest turns when needed;
- converts the result to provider wire format;
- returns a `Request` with a `BuildReport`.

Admission does not mutate the conversation and does not write facts. A request
that still exceeds the budget is rejected rather than silently truncated.

## Run

`run()` appends the user fact, calls the provider, appends assistant and
tool-result facts, and executes tools. It is the only stage that writes during a
turn. Compaction and window fallback are decisions expressed as new log facts.

## Package map

- `core/types.py`: log, message, block, and error types.
- `core/log.py`: append, lock, tail repair, session listing, copy/fork.
- `core/projection.py`: deterministic conversation projection.
- `core/request.py`: budget, admission, wire conversion.
- `core/loop.py`: provider stream, tool execution, retries, facts.
- `core/compaction.py` and `core/window.py`: context reduction decisions.
- `tools/workspace.py`, `tools/history.py`: workspace tools and evidence
  retrieval.
- `safety/policy.py`, `safety/shell.py`, `safety/grants.py`,
  `safety/approval.py`, `safety/review.py`: permissions and approvals.
- `extensions/skills.py`, `extensions/memory.py`, `extensions/hooks.py`,
  `extensions/mcp.py`, `extensions/subagents.py`, `extensions/snapshots.py`,
  `extensions/goal.py`: add-ons and durable host state.
- `app/config.py`, `app/provider.py`, `app/auth.py`, `app/prompts.py`,
  `app/instructions.py`, `app/note.py`: startup and request context.
- `hosts/cli.py`, `hosts/web.py`, `docs.py`: hosts and documentation.

Mini owns `cade_mini.ai` and `cade_mini.execution_env` as lower layers for
provider protocols and local process execution. It does not depend on the
separate Cade project. `test_mini_boundary.py` rejects old Cade imports and
application dependencies in these lower layers. There are no top-level
re-exports; import the owning submodule directly.

## Why the split

The split prevents a common failure: the request builder starts hiding
messages, then the history rewrite becomes the only copy of what happened. Mini
keeps the original facts and lets the projection and admission be replaced
without losing history.
