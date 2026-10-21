# Architecture

Cade is a standalone coding agent in `src/cade/`. It uses a pipeline
with four stages. Each stage has one owner and one direction of data flow.

```text
Session log -> Conversation projection -> Request -> Run
```

## Session log

`SessionLog` is an append-only JSONL file. Each entry has an ID, timestamp,
type, parent ID, and data. The active branch ends at the latest entry. `/tree`
appends a navigation event whose parent identifies the continuation point;
alternatives remain in the same file. The loop follows only that active path.

The log stores facts:

- `system`, `user`, `assistant`, and `tool_result` are model-visible facts.
- `compaction` records a summary and `first_kept_id`.
- `window` records a new context start and an optional handoff.
- `request`, `run`, `attempt`, `branch`, `checkpoint`, and `restore` are audit facts. They never become
  conversation messages. A branch event also preserves a selected input for
  editing after restart.

The schema is closed. See [Session log schema](reference/session-schema.md).

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
- preserves every projected message and tool result;
- checks the complete request against the input budget;
- converts the result to provider wire format;
- returns a `Request` with a `BuildReport`.

Admission does not mutate the conversation and does not write facts. Only
compaction and window events change the visible history. An over-budget request
is rejected rather than silently truncated.

## Run

Before appending a new user fact, `run()` persists interrupted results for
unfinished tool calls. It does not repeat tools whose outcome is unknown.
It then calls the provider, appends assistant and tool-result facts, and executes
tools. It is the only stage that writes during a turn. Compaction and window
fallback are decisions expressed as new log facts.

## Package map

- `core/types.py`: log, message, block, and error types.
- `core/events.py`: strict event payload validation.
- `core/log.py`: append, lock, tail repair, navigation, session listing, fork/clone.
- `core/history.py`: pure full-tree projection for history selection.
- `app/recovery.py`: turn checkpoints, LIFO actions, and coordinated recovery.
- `extensions/recovery.py`: action journal and pending transaction records.
- `core/projection.py`: deterministic conversation projection.
- `core/request.py`: budget, admission, wire conversion.
- `core/loop.py`: provider stream, tool execution, retries, facts.
- `core/compaction.py` and `core/window.py`: context reduction decisions.
- `tools/workspace.py`, `tools/history.py`: workspace tools and evidence
  retrieval.
- `safety/policy.py`, `safety/shell.py`, `safety/grants.py`,
  `safety/approval.py`, `safety/review.py`: permissions and approvals.
- `extensions/skills.py`, `extensions/memory.py`, `extensions/mcp.py`,
  `extensions/subagents.py`, `extensions/snapshots.py`, `extensions/goal.py`:
  add-ons and durable host state.
- `app/config.py`, `app/provider.py`, `app/auth.py`, `app/prompts.py`,
  `app/instructions.py`, `app/note.py`: startup and request context.
- `hosts/cli.py`, `docs.py`: hosts and documentation.

Cade owns `cade.ai` and `cade.execution_env` as lower layers for
provider protocols and local process execution. It does not depend on the
earlier Cade project. There are no top-level re-exports; import the owning
submodule directly.

## Recovery

Hosts inject a `TurnLifecycle` into the core loop. It creates a root-turn
checkpoint before the user fact, links it after that fact, and closes it after
execution. Tool workers and synchronous subagents inherit capture ownership.
The core does not import the workspace store. Recovery serializes cooperating
writers with a workspace lock in addition to the session run lock.

The native snapshot store retains both file images. An append-only workspace
action journal drives stepwise undo/redo; the session log remains authoritative
for conversation ancestry. A durable pending plan coordinates files and the
conversation commit. Recovery resumes idempotently after interruption and stops
on unknown external edits. See [Snapshots and goals](snapshots-goals.md).

## Lower layers and state

`ai/` owns provider protocols, streaming adapters, model metadata, and OAuth.
`execution_env/` owns local process execution and output capture. Neither layer
imports application or host modules. The AI layer supports only the Responses
protocol, as recorded in [decision 0007](decisions/0007-responses-only-provider.md),
and uses Sign in with ChatGPT for plan usage, as recorded in
[decision 0008](decisions/0008-siwc-login.md). DeepSeek uses a separate
transport with the same Responses stream format and its own endpoint.
`execution_env/sandbox.py` owns bubblewrap and Seatbelt process wrapping.
`core/permissions.py` carries structured approval results and per-call scopes.
The CLI confines shell and file workers on Linux/macOS; Windows runs directly.
See [Tools and security](tools-security.md) for boundaries and trusted extensions.

Credentials and user skills use `~/.cade/`. The SIWC host ID lives in
`~/.cade/host.json`. Sessions, snapshots, goals,
and project skills use `<workspace>/.cade/`. Project settings live in
`cade.json`. The earlier Cade project's state formats are not compatible with
this project; start from clean `~/.cade/` and workspace `.cade/` directories.

See the [code map](design/code-map.md) and
[persistence catalog](design/persistence-catalog.md) for implementation locations
and durable state ownership.

## Why the split

The split prevents a common failure: the request builder starts hiding
messages, then the history rewrite becomes the only copy of what happened. Cade
keeps the original facts and lets the projection and admission be replaced
without losing history.
