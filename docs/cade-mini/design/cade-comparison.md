# Cade comparison

Cade Mini is a smaller implementation of the same idea, not a fork with features
removed one by one. It keeps the log-first core and reuses Cade's provider and
execution layers. The rest is independent.

## Scale

Measured from the current checkout:

| Measure | Cade | Cade Mini |
| --- | ---: | ---: |
| Python files | 221 | 38 |
| Product lines | 41,810 | 6,050 |
| Test files | 33 | 30 |
| Test lines | 6,115 | 3,877 |
| Mini share of product code | — | 14.5% |

Cade's product code is concentrated in `coding_agent` (15.3k) and `harness`
(16.5k), with `agent` (4.7k), `ai` (3.9k), and `server` (1.0k). Mini's core
pipeline is about 1.7k lines; hosts, docs, and extensions make up the rest.

Line count is not a quality score. Cade carries a full product surface; Mini
carries a small, inspectable surface.

## Shared layers

Mini imports only:

- `cade.ai`: provider streaming, model resolution, credentials, usage.
- `cade.harness.execution_env`: subprocess execution and bubblewrap.

Everything above those layers is Mini code: `SessionLog`, projection, request
builder, loop, tools, policy, compaction, history, skills, memory, hooks, MCP,
subagents, snapshots, goals, docs, CLI, and web host.

## Capability comparison

| Area | Cade | Cade Mini | Status |
| --- | --- | --- | --- |
| Hosts | TUI, exec, browser, Python integration | CLI one-shot/REPL, local web host | Difference |
| Providers | Cade `ai` adapters, model picker, roles, effort | Same adapters; one configured model per run | Difference |
| Sessions | Tree, fork, clone, rewind, artifacts, inbox, steering | Tree, fork, clone, rewind, resume/list, undo-N; no artifacts or steering | Difference |
| Context | Full context policy, artifact previews, memory catalog, rollover, goal judge | Budget, pruning, history, compaction, window fallback | Difference |
| Tools | read/edit/write/grep/glob/find/ls/bash, websearch/webfetch, question, delegate, history, save_memory | Six workspace tools plus history, skill, memory, goal, subagent, delegate, MCP | Gap |
| Parallel tools | Parallel read batches with serial barriers | Sequential tool calls | Difference |
| Edit safety | Stale-write check, multi-edit, read-before-edit | One exact replacement, read-before-edit by convention | Gap |
| Permissions | Rule DSL, permanent/session grants, TUI approvals, sandbox | Modes, static rules, session grants, shell analysis; no OS sandbox | Difference |
| Extensions | MCP lifecycle, skills, memory, hooks, subagents | Persistent stdio MCP, skills, memory, hooks, subagents, delegate | Difference |
| Automation | exec JSONL, RPC, SDK, web | `--json` events, web JSON API | Gap |
| Input | TUI steering, queue, @ references, `!` shell, `$` skills | REPL steering/queue while a task runs, @ files, `!` shell, `$` skills | Difference |
| TUI | Full terminal workbench | REPL only | Difference |
| Docs | Product and developer docs | Design, subsystem, cookbook, history, generated catalogs | Comparable |

## Intentional differences

These are design choices, not unfinished code:

- No plugin system or dynamic composition. Mini's seams are fixed contracts.
- No separate TUI. The CLI REPL is the terminal host.
- No RPC or SDK. `--json` and the web API are the automation surfaces.
- No artifact store. Tool results are capped and recoverable through history.
- No permanent grants. Durable policy belongs in config rules.
- No parallel tool scheduler. Calls run sequentially to keep policy and logging
  simple.
- No OS sandbox. Mini relies on path boundaries, modes, shell classification,
  and approvals.

## Gaps that matter for parity

These are behaviors Cade users may rely on:

- Fork, clone, tree navigation, rewind, and multi-turn undo. Mini has the tree
  data but only an active-branch CLI.
- Web search and fetch, structured questions, and the `find`/`ls` tools.
- Stale-write detection and multi-edit. Mini's one-match edit is safe but less
  capable.
- Full rule DSL and permanent grants. Mini's rules cover common path and command
  prefixes.
- MCP resources, prompts, and server-initiated requests. Mini implements
  initialize, list, and call.
- Mid-run steering and queueing. Mini reads input on a separate thread and
  applies `/steer` at the next model step; tools in flight are not interrupted.
  Cade's TUI has richer line editing and status rendering.

## How to choose

Use Cade when you need the full terminal product, branching, web research,
parallel tools, or the existing session ecosystem. Use Cade Mini when you want a
small, deterministic core that is easy to audit, embed, extend, or teach.

The parity acceptance covers a bug fix, a feature addition, and resume. It does
not claim feature-for-feature equality. See [Parity](../parity.md) for the
workflow evidence and [Code map](code-map.md) for the package relationship.
