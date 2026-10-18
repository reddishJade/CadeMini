# Cade Mini: a full refactor of Cade's core

> Status: proposal, revised after review. This is a plan, not a reduced feature
> list. Cade Mini is the same product idea with a corrected structure.

## Summary

Cade Mini reimplements Cade on one pipeline: a session log records facts, a
deterministic projection derives model-visible history, a request builder turns
that history plus current inputs into one request, and a loop executes tools.
Everything else attaches to that pipeline through explicit seams.

Mini is not a smaller Cade. It is a full replacement that keeps Cade's
capabilities, removes redundant layers, and adds features only when the pipeline
can carry them. The first increment is the smallest version that proves the
pipeline end to end on the maintainer's daily provider. It is not the final
feature set.

Three corrections from the first draft:

- There is no line budget. The requirement is simple, direct, efficient, and
  elegant code. A structural review decides whether a change belongs, not a line
  count.
- The first increment supports the maintainer's Codex account. A Codex path is
  not a later phase.
- Compaction and window switching are part of the first increment. They are
  projection semantics, not optional features. Compaction is the default;
  window switching is explicit and is the fallback when compaction cannot help.

## Implementation status

Committed on `experiment/cade-mini`:

- Done: session schema, types, append-only log, deterministic projection, pure
  request admission, agent loop, Codex provider and credential store, six
  workspace tools with a policy, compaction and window reset, CLI/REPL with
  resume, project instructions, session listing/titles/`--continue`, project
  configuration, session approval, history retrieval, tool-result pruning,
  iterative compaction, automatic window fallback, skills, composable prompt
  modules, NOTE working state, plan/build/act modes, static rules, session
  grants, shell command classification, an optional Linux bubblewrap sandbox,
  independent subagents, durable project memory, tool lifecycle hooks, a
  persistent stdio MCP bridge, file snapshots/undo covering common shell
  mutation targets, workspace goals, automatic approval review, a local web
  host, and 198 tests.
- A real CLI acceptance run through the configured DeepSeek account passed: the
  model used the write and read tools, `hello.txt` contained `mini`, and a
  resumed run on the same session read the file and answered `mini`.
- A second real run used `cade-mini.json` and `AGENTS.md`; the model followed the
  project instruction and answered `ZEBRA`. `--list-sessions` showed the session
  title derived from the first user input.
- A real compaction run passed: the first turn produced 21k characters, the
  second turn triggered compaction, and the resulting projection was
  `system, summary, user, assistant` with a completed answer.
- A real skills run passed: the model called the `skill` tool, loaded the
  `orange` skill, and answered `ORANGE`; NOTE.md and AGENTS.md were in context.
- A real build-mode run passed: without `--yes`, the model used the write tool
  and created `build.txt` with content `build-mode`.
- A real web-host run passed: an HTTP POST to `/api/task` returned
  `{"answer":"WEB-OK","termination":"completed"}` from DeepSeek.
- Parity acceptance passed on two repository workflows and a session resume:
  Mini and Cade produced the same bug-fix and feature diffs, both ran pytest,
  and both recalled the earlier fix. See [cade-mini-parity.md](cade-mini-parity.md).
- A persistent MCP subprocess test discovers `echo` and `counter` tools, calls
  them across event loops, and gets `echo:hi` plus stateful `count:1`/`count:2`.
- A live Codex smoke reached the API but the account returned HTTP 429 "usage
  limit has been reached". The loop recorded `request` and `attempt` facts and
  terminated with `provider_error`; the provider and error paths are verified.
- On non-Linux hosts the OS sandbox is unavailable; path boundaries, mode
  policy, shell classification, and approvals remain the active safety layer.
- Snapshots cover write/edit and common single-command shell mutations; complex
  pipelines are not snapshotted.
- Remaining: live Codex acceptance when quota returns.

## 1. What Cade is today

Product Python is about 41,000 lines, plus about 5,000 lines of tests. The
repository has 1,387 commits since 2026-05-29.

| Area | Approx. lines | What it owns |
| --- | ---: | --- |
| Hosts: TUI, exec, web | 7,900 | prompt_toolkit UI, streaming presentation, HTTP/WebSocket server |
| Agent runtime | 4,700 | runs, inbox, composition generations, tool gate, goal, subagents |
| Agent core | 4,600 | loop, request assembly, context policy, tool scheduling |
| Security and sandbox | 4,200 | permission model, rule DSL, approvals, grants, shell analysis, bubblewrap |
| Coding tools | 3,400 | read, write, edit, glob, grep, bash, web, question, subagent |
| Session and undo | 3,200 | JSONL tree, replay, surface, artifacts, history retrieval, snapshots |
| Providers and auth | 4,000 | seven transports, streaming, usage, OAuth, model registry |
| Product services, config, CLI | 5,900 | assembly, commands, completion, settings, config wizard, entry point |
| Extensions | 3,500 | MCP, skills, memory, hooks, observability |

User-visible capabilities:

- Session continuity: project sessions, resume, fork, clone, tree, rewind, undo,
  snapshots.
- Context: dynamic sections, NOTE handoff, token budgets, evidence previews,
  artifacts, automatic rollover, history search and exact read.
- Execution: plan/build/act modes, rule DSL, approval routers, session and
  permanent grants, LLM approval reviewer, Linux bubblewrap sandbox.
- Models: OpenAI Chat/Responses/Codex, DeepSeek, GLM, MiMo, custom; main,
  subagent, reviewer, judge, and refiner profiles.
- Tools: read, write, edit, glob, find, ls, grep, bash, webfetch, websearch,
  question, history, memory, skills, MCP, subagents.
- Hosts: terminal UI, noninteractive exec, browser workspace.
- Operations: audit JSONL, correlation IDs, provider request records, external
  hooks.

The pipeline itself is about 7,000 lines. The other 34,000 lines are product
surface, safety policy, hosts, and workarounds for the back-edges described in
section 4.

## 2. The core idea and the primary invariant

One sentence: **Cade keeps a durable, inspectable record of what happened, and
gives the model a deterministic view of that record, while effects happen only
through typed tools.**

The working rule is the one Dsh states as **model-visible means logged**: every
model-visible message must be reconstructable from the session log. A new
model-visible input needs a logged fact. A log-only record is allowed, but it
never projects into model history.

Four invariants:

1. **Facts are durable and append-only.** User input, model output, tool results,
   and compaction decisions are recorded before they are relied on. Recovery is
   re-derivation, not reconstruction from process memory.
2. **History is a projection.** `project(log_branch) -> conversation` is pure,
   deterministic, and independent of process state. It contains no stored
   projection and no mutable working copy.
3. **A request is a snapshot.** `build(conversation, context, tools, budget) ->
   request` is pure. It may drop content to fit the budget and reports what it
   dropped. It never writes history.
4. **Effects go through tools under policy.** The loop decides what to call; the
   policy decides whether it may start; the workspace is the effect surface.

The differentiating part is invariants 1 and 2. A streaming loop, tool parsing,
and file tools are table stakes. The lossless transcript behind a lossy window,
the branchable session, and the auditable request are Cade's identity.

### Three planes, one log

The log contains three kinds of records. Only the first is projected:

| Plane | Examples | Projected? | Purpose |
| --- | --- | --- | --- |
| Conversation | `user`, `assistant`, `tool_result`, `system`, `compaction` | Yes | Model-visible history |
| Request | `request` | No | Exact request envelope, dynamic context, token estimate, dropped ids |
| Operational | `run`, `attempt`, `window`, `meta` | No | Lifecycle, failures, hard boundaries, metadata |

`window` is a decision that changes where projection starts. It is log-only
because it produces no message of its own; an optional handoff summary is a
separate conversation fact.

The latest `system` event is always projected first and is never shadowed by
compaction. Log a new one when the base prompt changes. Dynamic context such as
the environment, `AGENTS.md`, and the current mode is request-only.

This separation satisfies "model-visible means logged" without pretending that
the current environment, `AGENTS.md`, or a failed attempt is conversation.

### Record shapes

One JSON object per line. A branch is a parent chain; the head is the last entry.

```json
{"v":1,"id":"e1","parent":null,"ts":"...","type":"meta","data":{"session":"s1","project":"C:/repo"}}
{"v":1,"id":"e2","parent":"e1","ts":"...","type":"user","data":{"text":"Fix the test","source":"user"}}
{"v":1,"id":"e3","parent":"e2","ts":"...","type":"assistant","data":{"blocks":[{"type":"text","text":"..."},{"type":"tool_call","id":"c1","name":"bash","args":{"command":"pytest -q"}}],"model":"...","provider":"...","usage":{"input":1200,"output":80},"provider_state":null}}
{"v":1,"id":"e4","parent":"e3","ts":"...","type":"tool_result","data":{"call_id":"c1","status":"ok","content":"3 passed"}}
```

A `system` event carries `{text}`; the latest one is projected first. A `summary`
message is emitted from a `compaction` event, not stored as a message.

## 3. The one-way pipeline

```text
Session log (conversation + request + operational facts)
      │  project()            pure; reads conversation facts and window decisions
      ▼
Conversation (immutable)
      │  build()              pure; adds request-only context; admits to budget
      ▼
Request (ephemeral) ────────► provider
      │
      ▼
Tool calls ──► effects ──► tool results ──► append to log
```

Definitions:

- `Log` is an ordered set of typed facts on a branch.
- `Project(log) -> Conversation` is pure and deterministic. It rejects a
  malformed branch with a specific error.
- `Build(conversation, context, tools, budget) -> Request` is pure. It reports
  what it dropped; it does not write.
- `Run(log, input) -> events + new facts` is the only writer.

Rules:

- **R1. One fact, one record.** A tool call appears in the assistant fact, not
  twice. The final answer is the last assistant text, not a separate fact.
- **R2. No derived content in the log.** A stored message list, a rendered
  preview used as history, or a display summary is not a fact. Large tool output
  may live in an immutable blob; the log stores a stable reference, and the
  projection resolves it.
- **R3. Decisions are facts; projections are not.** A compaction boundary or a
  window start may be logged. The messages they produce are recomputed.
- **R4. Request context is logged but not projected.** The base system prompt is
  a `system` conversation fact when it changes. Dynamic context, the model
  route, tool schemas, token estimates, and dropped ids are request facts.
- **R5. Failures are log-only.** A failed attempt is an `attempt` fact. It does
  not become a fake assistant message.
- **R6. Budget admission belongs to the request.** Compaction belongs to the
  loop, because it appends a fact. A request builder never calls a model.

## 4. Current leaks

These are the back-edges that make the current code hard to maintain.

1. **The projection is written into the log.** A rollover stores the complete
   encoded replacement surface in `context_window_reset`
   ([recorder.py](../src/cade/harness/session/recorder.py) `record_context_window_reset`,
   [surface.py](../src/cade/harness/session/surface.py) applies `replacement` at
   replay). Replay then depends on output produced by an older version of the
   projection code, not only on facts.
2. **Request assembly mutates history.** `DefaultRequestAssembler.assemble`
   rewrites `context.messages` in place
   ([request.py](../src/cade/agent/request.py)). `call_provider` appends a
   `ContextMessage` to history while sending the request
   ([_provider.py](../src/cade/agent/_provider.py)). Request construction is
   therefore also a state update.
3. **One fact is recorded several times.** The final answer is stored again in
   the `final` event and in an `assistant` record, on top of the assistant
   event's text; replay de-duplicates it with a heuristic
   ([recorder.py](../src/cade/harness/session/recorder.py),
   [surface.py](../src/cade/harness/session/surface.py) `_append_plain_assistant`).
   Tool calls are stored inside the assistant event and again as `tool_use`
   events ([events.py](../src/cade/harness/agent_runtime/events.py)), then
   de-duplicated by call ID at replay.
4. **Three event vocabularies.** `AgentEvent` becomes `AgentHarnessEvent`, then a
   versioned session envelope ([event_codec.py](../src/cade/harness/session/event_codec.py)).
   Each boundary is a translation layer that can drift from the log.
5. **History is a mutable working state.** `ContextManager.history` holds both
   the projection and the loop's writes; replay loads into it
   ([context_manager.py](../src/cade/agent/context_manager.py),
   [replay.py](../src/cade/harness/session/replay.py)). There is no runtime
   boundary between "what the log says" and "what this process currently has".

Pi and Dsh show the alternative:

| Concern | Current Cade | Pi | Dsh | Cade Mini |
| --- | --- | --- | --- | --- |
| Compaction record | Full replacement message list | `compaction` entry with `summary` and `firstKeptEntryId` | `compaction/*` events plus a typed `user/message` replace | `compaction` event with `summary` and `first_kept_id` |
| Projection | Adopt stored list | Summary plus kept entries | Fold surface operations | Summary plus kept entries |
| Original facts | Kept but bypassed | Kept | Kept | Kept |
| Determinism | Depends on old projection | Depends on facts | Depends on facts | Depends on facts |

The lesson is not "add a surface layer". It is "store the decision and the
summary, never the projected result".

## 5. Compaction and window

Compaction and window switching are projection semantics. They belong in the
first increment because a coding session outgrows one model window. The full
event schema and storage contract are in
[cade-mini-schema.md](cade-mini-schema.md).

### Compaction

A compaction is a conversation fact and a decision:

```json
{"v": 1, "id": "e80", "parent": "e79", "ts": "...", "type": "compaction",
 "data": {
   "summary": "## Goal\n...\n## Progress\n...\n## Next steps\n...",
   "first_kept_id": "e52",
   "tokens_before": 48210,
   "model": "gpt-5.6-luna",
   "provider": "openai-codex",
   "usage": {"input": 43000, "output": 1200},
   "details": {"read_files": ["src/a.py"], "modified_files": ["src/b.py"]}
 }}
```

Projection rule:

1. Project the latest `system` event first. It is never shadowed.
2. Find the latest `compaction` on the branch.
3. Emit its `summary` as a `summary` message. The wire adapter renders it as a
   user-role message wrapped in `<compaction-summary>`.
4. Project conversation facts from `first_kept_id` to the head.
5. Ignore the shadowed facts for this projection. They remain readable in the
   log.

Compaction is chosen at a user-turn boundary so a tool call and its result never
cross the cut. The retained tail starts with a complete user turn. If the latest
turn alone exceeds the budget, use a window instead of splitting the turn.

Repeated compaction is iterative: the next compaction starts after the previous
one, includes the previous summary in its input, and appends a new summary with a
new `first_kept_id`. Projection uses only the latest compaction.

### Window

A window is a hard start with an optional handoff:

```json
{"v": 1, "id": "e90", "parent": "e89", "ts": "...", "type": "window",
 "data": {"start_id": "e88", "reason": "manual", "handoff": "Optional note."}}
```

Projection starts at `start_id` after the latest `system` event. If `handoff` is
present, emit it as a summary message before the start. The window event itself
is log-only.

### When each runs

- Automatic compaction runs before a request when the estimated input exceeds
  `window - output_reserve - headroom`. It is the default behavior.
- Manual `/compact` forces compaction.
- Manual `/new-context` appends a window.
- Automatic window is the fallback when compaction cannot reduce enough: the
  latest turn alone is oversized, the summary call fails repeatedly, or the
  retained tail is still over budget. The fallback starts at the latest user
  turn and uses a static handoff note.

### Failure and crash

- The summary is a separate model call with no tools. Its usage, model, and
  provider are recorded on the compaction event.
- If the summary call fails, append an `attempt` fact and do not append a
  compaction. The conversation is unchanged. Report the failure.
- If the process crashes after the summary but before the compaction append, the
  log has no compaction. The next run retries.
- If the compaction event is appended, it is committed. Recovery projects the
  summary and the retained tail.
- An open tool call at the head blocks compaction until its result is recorded
  or the run is finalized with an interrupted result.

### What not to store

Do not store the projected message list, the rendered system prompt per request,
or a "replacement surface". Store the summary, the cut point, and the usage. That
is enough to re-derive the conversation.

## 6. First increment

The first increment proves the pipeline and supports daily Codex use. Features
that do not shape the pipeline wait.

### Structural scope

| Capability | Included |
| --- | --- |
| Log | Append-only JSONL, parent-linked branch, base system prompt event, session metadata, resume, lock, fsync |
| Projection | Pure `project`, tool pairing, compaction, window, summary messages |
| Request | Pure `build`, dynamic context, tools, budget admission, dropped-id report |
| Loop | Streaming, tool calls, sequential execution, cancellation, step limit, retry, overflow recovery |
| Compaction | Automatic pressure trigger, manual `/compact`, summary fact, iterative compaction |
| Window | Manual `/new-context`, automatic fallback, handoff note |
| Provider | Codex/Responses first by reusing the existing adapter and auth; chat-completions alongside if cheap |
| Tools | `read`, `write`, `edit`, `glob`, `grep`, `bash` |
| Policy | Workspace path boundary, allow/deny/ask, CLI approval prompt, `--yes`, `--read-only` |
| CLI | `cade-mini exec`, REPL, `--resume`, `--session`, `--json`, `/compact`, `/new-context`, `/context`, `/exit` |
| Tests | Log round-trip, projection golden with compaction, request purity, fake-provider loop, crash/resume, path escape |

Assistant facts carry an opaque `provider_state` for Responses/Codex replay.
Chat-completions adapters leave it null. Provider-specific state never enters the
conversation vocabulary; it is a field on the fact.

### Deliberately deferred

These are in scope for Mini. They wait because they do not shape the pipeline, or
because a simpler mechanism already covers the need:

- Session list, titles, fork, clone, tree, rewind, `--continue`.
- Plan/build/act modes, rule DSL, grants, shell analysis, full sandbox.
- `AGENTS.md` and project instructions, prompt modules, skills.
- Subagents, memory, MCP, external hooks, web.
- TUI, setup wizard, config browser.
- LLM approval reviewer, goal judge.
- Snapshot undo. Start with Git; add snapshots only if Git is insufficient.
- Multiple model profiles. Add them when the roles that need them exist.

### Acceptance criteria

1. A real task on a real repository completes with the six tools through Codex.
2. The log is append-only: no rewrite, no edit of an existing line, `fsync` on
   append.
3. Two processes projecting the same log produce identical messages and tool
   pairing, including after a compaction.
4. Request construction does not mutate the log or the projection.
5. A long synthetic session compacts automatically; the projection after
   compaction contains the summary and the retained tail; the shadowed facts
   remain in the log.
6. Kill the process between a tool call and its result, then `--resume`: the
   projection contains the completed facts and one synthetic `interrupted`
   result; the next request succeeds.
7. A path outside the workspace and a denied shell command have no effect and
   return an error result.
8. The log contains no replacement message list, no duplicate final answer, and
   no `tool_use` record outside its assistant fact.

### Delivery sequence

1. **Harvest audit.** Read the existing modules and classify them as keep,
   adapt, rewrite, or defer. Freeze the session schema and the compaction
   semantics.
2. **Log and projection.** Append-only log, branch walk, pure projection, tool
   pairing, golden tests. No model.
3. **Request and fake loop.** Pure request builder, budget admission, a fake
   provider that issues tool calls, cancellation and termination tests.
4. **Codex.** Wrap the existing Responses adapter and Codex auth, store opaque
   provider state on the assistant fact, run a real task.
5. **Compaction and window.** Summary call, automatic trigger, manual commands,
   crash and failure tests.
6. **Tools and policy.** Six tools, path boundary, approval prompt.
7. **CLI and resume.** One-shot, REPL, JSON events, crash/resume.
8. **E2E.** A real repository task, a long session that compacts, and a resumed
   session.

## 7. Harvest map

Do not rewrite a module because it is old. Rewrite it only when it violates the
pipeline or cannot be read in one sitting. The full audit is in
[cade-mini-harvest.md](cade-mini-harvest.md). This is the summary.

| Existing area | Verdict | Reason |
| --- | --- | --- |
| `ai/providers/responses.py`, `ai/auth/openai_codex.py` | Reuse behind a protocol | Auth and vendor replay are real complexity; wrap, do not rewrite for novelty |
| `ai/models.py` | Reuse | Static model windows and metadata |
| `agent/messages.py`, `agent/types.py` | Adapt | Simple shapes; add `provider_state`, remove unused fields |
| `agent/_tool_pairing.py` | Reuse | Small, pure, exactly the rule Mini needs |
| `agent/_context_window.py` | Adapt | Keep estimation; replace the anchor logic with a simpler last-usage anchor |
| `coding_agent/tools/{read,write,edit,glob,grep,bash}.py` | Adapt | Edge cases are valuable; strip render intents and permission metadata |
| `coding_agent/tools/shell_adapter.py` | Reuse | Shell detection is portable |
| `harness/execution_env/` | Reuse/adapt | Filesystem and shell primitives are clean; defer the Linux sandbox |
| `harness/session/store.py` | Split/rewrite | Keep log validation, branch walk, and tail recovery; rewrite metadata, fork, rewind, and index |
| `harness/session/recorder.py` | Rewrite | Records duplicate final and assistant facts and stores surface replacements |
| `harness/session/surface.py` | Rewrite | Core projection; current heuristics and stored replacement are the problem; keep `validate_tool_pairing` |
| `harness/session/replay.py` | Rewrite | Becomes a thin `project` call |
| `harness/session/artifacts.py` | Reuse/adapt | Clean blob, preview, and resolver; fits the store-a-reference rule |
| `harness/session/history.py` | Defer | Depends on compaction and window; add after they exist |
| `harness/session/inbox.py` | Defer/simplify | One input per turn first; steering later |
| `agent/request.py` | Rewrite | Core request builder; current one mutates and takes policy decisions |
| `agent/context.py` | Simplify | Keep a small context-provider seam; drop block priorities and assembly stages |
| `agent/context_policy.py` | Rewrite | Replaced by compaction and window |
| `agent/context_manager.py` | Delete | No mutable history; the projection is derived |
| `agent/agent_loop.py` | Rewrite | Core loop; current one has nested loops and many hooks |
| `agent/_execution.py` | Adapt/simplify | Sequential tools first; parallel batches later |
| `agent/_provider.py` | Adapt | Streaming, retry, and usage mapping are useful |
| `harness/agent_runtime/harness.py` | Rewrite | Runtime lifecycle; composition and gate complexity are not needed |
| `harness/agent_runtime/config.py` | Rewrite/simplify | 69 imports; hooks and product policy |
| `harness/agent_runtime/events.py` | Rewrite | One event vocabulary; no translation layer |
| `harness/agent_runtime/run_control.py` | Defer | Steering and queuing are later |
| `harness/agent_runtime/tool_gate.py`, `harness/security/` | Defer, then simplify | Simple policy first; rules, grants, and sandbox later |
| `coding_agent/modes/tui/`, `server/`, `interaction/` | Defer | Hosts come after the core |
| `harness/mcp/`, `skills/`, `memory/`, `subagents/` | Defer | Add as seams after the pipeline is proven |

Harvest criteria:

- One responsibility per module.
- A pure function where the design says pure.
- No hidden state owner.
- No second event vocabulary.
- No speculative abstraction; every interface has a current caller.
- A reader can trace a fact from log to request in one pass.

## 8. End state and feature order

Mini's end state covers Cade's capabilities that survive the seam test. The order
is dependency-driven:

1. **Core and Codex.** Log, projection, request, loop, compaction, window, six
   tools, policy, CLI.
2. **Daily continuity.** Resume list, titles, `--continue`, `/context`, JSON
   events, a second provider, an explicit config file.
3. **Project awareness.** `AGENTS.md` and instruction files, prompt modules,
   skills.
4. **Safety and modes.** Plan/build/act, grants, shell analysis, sandbox,
   approval UI.
5. **Isolation and integrations.** Subagents (separate logs), memory, MCP,
   external hooks.
6. **Hosts and advanced product.** TUI, web, snapshot undo, goal judge, approval
   reviewer.

Features that may be replaced rather than ported:

- **Provider fallback.** Cade already removed automatic fallback. Mini should not
  reintroduce it without a concrete failure it solves.
- **Snapshot undo.** Git already records workspace changes. Add per-turn
  snapshots only if Git cannot answer a real user need.
- **Three event vocabularies.** Replace with one event type. Hosts render it.
- **Composition generations.** Replace with "operation requires an idle agent"
  and a new provider on the next run.
- **Rule DSL.** Start with a small policy; grow the rule set only when a user
  need appears.

## 9. Simplicity rules

There is no line budget. These rules are the review standard:

- **One source of truth.** A fact has one home. If it can be derived, do not
  store it. If it is stored, do not keep a second copy.
- **One writer.** Each mutable state has one owner and one write path. The log is
  the only durable conversation writer. Projection and request builders never
  write.
- **One-way flow.** Log to projection to request to effects. A feature that adds
  a back-edge is rejected.
- **Pure at the center, I/O at the edges.** Projection and request construction
  are pure. File, process, network, and model access live in tools, providers,
  and hosts.
- **One vocabulary.** The loop emits the event types that the log stores. Hosts
  render them. No translation layer between two event models.
- **No speculative abstraction.** An interface exists because a current caller
  needs it. Delete unused layers.
- **Deletion removes code.** Removing a feature removes its branches, flags, and
  tests. A flag that keeps a dead path is a design failure.
- **Change face equals review face.** A change carries only the lines its
  behavior needs. A migration moves code; it does not rewrite it.

Review questions for each change:

1. What fact or state does this add? Where is it stored?
2. Who writes it? Is there a second writer?
3. Can it be derived from the log? If yes, why store it?
4. Does this function have side effects? Are they at the edge?
5. Does this add a new mutable state owner or a new conversation event type?
6. If this feature is deleted, what remains? If the answer is "flags and
   branches", the boundary is wrong.

A change that adds a new mutable state owner or a second conversation event type
needs a design review, not a patch.

## 10. What to take from Pi and Dsh

Take principles and contracts, not infrastructure. Pi and Dsh are local
checkouts; their package counts and process gates are their own product.

| Idea | Source | Mini decision |
| --- | --- | --- |
| Session is a tree; each path is a branch; the active branch supplies history | Pi `how-pi-works.md` | Keep `parent` on every entry; support one active branch first; add fork and rewind later |
| Compaction inserts a summary entry; original entries remain | Pi `compaction.ts` | Adopt: `compaction` event with `summary` and `first_kept_id` |
| All interfaces share one agent and session mechanism | Pi `how-pi-works.md` | Adopt: `exec`, REPL, and later TUI/web use the same `run` API and event stream |
| Do not preserve backward compatibility unless asked | Pi `AGENTS.md` | Adopt: new session format; no compatibility layer with current Cade logs |
| Model-visible means logged | Dsh `architecture.md` | Adopt as the primary rule |
| Log-only events never project | Dsh `subsystems/session.md` | Adopt: `request`, `run`, `attempt`, `window` |
| Typed surface operations (`append`, `replace`) | Dsh `subsystems/session.md` | Adapt: `first_kept_id` for compaction and `start_id` for window first; generalize only when a second use appears |
| Compaction is a log-recorded operation; the summary is a surface message | Dsh `subsystems/compaction.md` | Adapt: store summary and cut point; no lock bracket until concurrent writers exist |
| Capability seams: definition, provider, consumer | Dsh `architecture.md` | Adopt for provider, tool, context, policy, and host boundaries. Do not build a plugin runtime in the first increment |
| Defensive patterns: explicit correctness, one source of truth, registered seams, review face, evidence | Dsh `defensive-patterns/README.md` | Adopt as the review standard in section 9 |
| Decision records with alternatives and consequences | Dsh `.agents/notes/README.md` | Adopt a lightweight version: one file per structural decision under `docs/decisions/` |

Deliberately not copied:

- Cordis and the all-plugin runtime. Mini uses explicit protocols and dependency
  injection; the loop is a small kernel, not a plugin.
- Dsh's package matrix, bilingual documentation gates, and 293-note archive.
- Full surface-op algebra, source-event sets, and plugin-owned message
  projections. Add them only when a concrete feature needs them.
- Compaction lock brackets and concurrent-writer protocols. A single-process CLI
  does not need them; add them when a second writer exists.

## 11. Decisions

1. **Package boundary.** New `src/cade/mini/` with an import firewall. It may
   import the standard library, `cade.ai` (provider adapters, auth, model
   registry), and `cade.harness.execution_env` (filesystem and shell
   primitives). It may not import `cade.agent`, `cade.coding_agent`, or any
   other `cade.harness` package. The current Cade stays runnable until Mini
   reaches parity.
2. **First provider.** Codex/Responses, reusing the existing adapter and auth
   behind Mini's `Provider` protocol. Chat-completions can ship alongside it if
   the adapter is cheap; it is not a prerequisite.
3. **Compaction.** Pi-style `summary` plus `first_kept_id`, stored as a
   `compaction` event. Default automatic compaction. Explicit window plus
   automatic fallback. Never store the projected replacement.
4. **Session format.** A new format, versioned from day one. No compatibility
   layer with current Cade sessions. Import only if a real need appears.
5. **Host.** CLI plus REPL first. TUI and web are later hosts on the same event
   stream.
6. **Entry point.** `cade-mini` during development. Switch `cade` only after the
   acceptance criteria pass on a real workflow.
7. **Process.** The first task is the harvest audit and the schema decision
   record. Every structural decision gets a short record with problem, decision,
   alternatives, and consequences.

## 12. Non-goals

- Storing derived projections, rendered history, or replacement message lists.
- Automatic model fallback.
- LLM-based permission decisions in the first increment.
- A plugin runtime in the first increment.
- Backward compatibility with Cade's current session format.
- Feature parity before the pipeline is proven. Parity is the goal; the order is
  the plan.

## References

Local checkouts, not part of this repository:

- Pi: `~/workspace/pi/packages/coding-agent/docs/how-pi-works.md`,
  `packages/coding-agent/src/core/compaction/compaction.ts`,
  `packages/coding-agent/src/core/session-manager.ts`, root `AGENTS.md`.
- Dsh: `~/workspace/dsh/docs/architecture.md`,
  `docs/subsystems/session.md`, `docs/subsystems/compaction.md`,
  `docs/defensive-patterns/README.md`, `.agents/notes/README.md`,
  root `AGENTS.md`.
