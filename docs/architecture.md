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
- `request`, `run`, `attempt`, `branch`, `checkpoint`, `restore`, and `tool_execution` are audit facts. They never become
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

## Package boundaries

Cade has five runtime packages:

- `ai`: model protocols, providers, model metadata, and authentication protocols.
- `harness`: the log-driven agent loop, session facts and projections, request
  admission, tool execution contracts, and internal execution backends.
- `coding`: programming tools, project context, workspace policy, subagents,
  MCP integration, snapshots, and coordinated workspace recovery.
- `app`: configuration, credentials, provider assembly, application sessions,
  settings, and commands.
- `hosts`: CLI arguments, terminal I/O, and TUI presentation.

Dependencies point from hosts to app, coding, harness, and AI. Each layer may
use lower layers; lower layers never import higher layers. The harness includes
agent runtime behavior; there is no parallel agent package. Local process and
sandbox implementations live inside `harness/execution/local`, not at the
package root. Execution scope contracts do not import those platform backends.
`tests/test_architecture.py` checks these boundaries.

`app/bootstrap.py` creates the same `ApplicationSession` for one-shot and TUI
execution. `app/session.py` owns active-session operations; it does not import
terminal modules. `harness/executor.py` handles authorization, per-call scope,
timeouts, and tool errors for both model calls and direct application calls.
The loop remains responsible for recording model-turn facts and emits completed
tool events after recording results.

`coding/tools/run_code.py` exposes one-shot Python tool orchestration. The executor
injects the current tool set and a nested execution callback; the caller records
nested dispatch and outcome facts without projecting intermediate results.
`harness/execution/local/python.py` owns the fresh interpreter, bounded control
messages, output, and process cleanup. Direct Python I/O uses the shell sandbox;
nested tool writes retain the current turn's recovery ownership.

See the [code map](design/code-map.md) for implementation locations. Cade does
not depend on the earlier Cade project and provides no compatibility import
paths. Import types and functions from their owning modules.

## Recovery

Application assembly injects a `TurnLifecycle` into the harness loop. It creates a root-turn
checkpoint before the user fact, links it after that fact, and closes it after
execution. Tool workers and synchronous subagents inherit capture ownership.
The harness does not import the workspace store. Recovery serializes cooperating
writers with a workspace lock in addition to the session run lock.

The native snapshot store retains both file images. An append-only workspace
action journal drives stepwise undo/redo; the session log remains authoritative
for conversation ancestry. A durable pending plan coordinates files and the
conversation commit. Recovery resumes idempotently after interruption and stops
on unknown external edits. See [Snapshots and goals](snapshots-goals.md).

## Lower layers and state

`ai/` owns provider protocols, streaming adapters, model metadata, and OAuth.
`harness/execution/` owns execution scopes, local process execution, and output
capture. Neither AI nor the harness imports application or host modules. The AI layer supports only the Responses
protocol, as recorded in [decision 0007](decisions/0007-responses-only-provider.md),
and uses Sign in with ChatGPT for plan usage, as recorded in
[decision 0008](decisions/0008-siwc-login.md). DeepSeek uses a separate
transport with the same Responses stream format and its own endpoint.
`harness/execution/local/sandbox.py` owns bubblewrap and Seatbelt process wrapping.
`harness/permissions.py` carries structured approval results and per-call scopes.
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
