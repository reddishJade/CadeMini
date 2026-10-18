# Cade Mini harvest audit

> Historical record from before the standalone project split. Package names,
> paths, and development plans below describe that earlier checkout. For the
> current project, see the [manual](../README.md) and
> [architecture](../architecture.md).

> Status: first pass, read-only. This audit classifies the existing code for the
> Mini refactor. It does not move or edit code.

## Method

Inventory: 220 modules and 47,624 lines under `src/cade/`, excluding tests. The
core pipeline modules were read directly. The largest vendor adapter and the host
packages were assessed from their public structure and key sections rather than
line by line; a later per-file pass confirms them when their phase starts.

Verdicts:

- **Reuse**: call it behind a Mini protocol with no logic change.
- **Adapt**: keep the logic, change the types and interface to Mini's.
- **Rewrite**: the module violates the pipeline or is a wiring hub.
- **Defer**: valuable, but not needed for the first increment.
- **Delete**: no place in Mini.

A module can be split. `harness/session/store.py` keeps its low-level log
primitives and loses its metadata, fork, rewind, and index class.

## Summary

| Area | Approx. lines | Verdict | Note |
| --- | ---: | --- | --- |
| `ai/` | 4,500 | Reuse, adapt resolver | Cleanly isolated; no upward imports |
| `agent/` | 5,300 | Rewrite the pipeline, adapt the edges | Request, context, policy, and loop are the back-edges |
| `harness/session/` | 3,200 | Split: reuse primitives, rewrite store and projection | The log schema changes |
| `harness/agent_runtime/` | 5,400 | Rewrite runtime, defer features | Harness, events, and config are wiring hubs |
| `harness/security/` | 4,000 | Defer, keep vocabulary | A 150-line policy replaces the first-increment need |
| `harness/execution_env/` | 800 | Reuse, defer Linux sandbox | Self-contained leaf package |
| `coding_agent/tools/` | 3,900 | Adapt six, defer the rest | Logic is good; wrappers carry old types |
| `coding_agent/` product and hosts | 15,200 | Rewrite the root, defer hosts | App, assembly, TUI, exec, server |
| Extensions | 4,700 | Defer as seams | MCP, skills, memory, hooks, snapshot |
| Tests (separate) | 5,300 | Harvest contracts | Port cases, not module fixtures |

The product rows sum to roughly 47,600 lines. The test row is separate and is not
part of that total.

Approximate totals: reuse about 4,500 lines, adapt about 5,500, rewrite about
6,500, defer about 29,000, delete about 1,500. The totals are rough because a
module can contribute to more than one verdict.

The important result is not the count. It is the shape: the pipeline is a small
part of the code, and most of the rest is separable.

## Core verdicts

### `ai/` — reuse

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `providers/base.py` | 102 | Reuse | Minimal stream protocol; Mini can depend on its shape |
| `events.py` | 108 | Reuse | Stream deltas, tool calls, usage, failure |
| `types.py` | 202 | Reuse | `StreamOptions`, `ToolDefinition` |
| `models.py` | 382 | Reuse | Model windows and aliases |
| `resolver.py` | 207 | Adapt | Model and transport resolution; take the parsing, not the product policy |
| `providers/responses.py` | 1,176 | Reuse behind a protocol | Vendor complexity, not pipeline complexity. Do not rewrite for the first increment |
| `providers/_compat.py` | 338 | Reuse | Chat Completions base |
| `providers/_codec.py` | 330 | Reuse | Message and tool encoding |
| `providers/_runtime.py` | 150 | Reuse | Retry, rate limit, error classification |
| `auth/openai_codex.py` | 349 | Reuse | Codex OAuth |
| `auth/*` | 184 | Reuse | Credential storage and resolution |
| `usage.py`, `cache.py` | 224 | Reuse | Usage totals and cache stats |

The `ai/` package imports no `cade.agent`, `cade.harness`, or `cade.coding_agent`
module. It is a clean lower layer.

`responses.py` is the one large module with justified complexity. It decodes the
Responses stream, merges replay items, and handles Codex headers. Rewriting it
would risk the daily Codex path. Wrap it and add a contract test with a recorded
payload. A later pass can delete dead branches if the protocol permits.

### `agent/` — rewrite the pipeline, adapt the edges

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `messages.py` | 101 | Adapt | Keep the message union; add an opaque `provider_state`; use `SummaryMessage` for compaction |
| `types.py` | 397 | Adapt | Keep content blocks, `ToolSpec`, `AgentToolResult`; move render intents and approval types to hosts and policy |
| `config.py` | 197 | Adapt | Keep a small context and loop config; drop hooks Mini does not need |
| `_tool_pairing.py` | 51 | Reuse/copy | Pure and exactly the pairing rule Mini needs |
| `_context_window.py` | 139 | Adapt | Keep token estimation; replace the Responses-specific anchor with a simple last-usage anchor |
| `_codec.py` | 157 | Adapt | One wire conversion per provider; make `provider_state` explicit |
| `_provider.py` | 391 | Adapt | Keep streaming, retry, usage; remove `ContextMessage` appends and Cade metrics |
| `_execution.py` | 653 | Adapt/simplify | Sequential tools, timeout, cancellation, error results first; parallel batches and hooks later |
| `events.py` | 135 | Rewrite | Replace with Mini's log events; streaming deltas stay ephemeral |
| `context.py` | 699 | Rewrite/simplify | Keep `InstructionCollector` and `NotesCollector` as providers; drop block priorities and staged assembly |
| `context_manager.py` | 249 | Delete | No mutable history; projection derives it |
| `context_policy.py` | 548 | Rewrite | Replaced by compaction, window, and a simple budget |
| `request.py` | 592 | Rewrite | Pure builder; no mutation, no model calls, no policy decisions |
| `agent_loop.py` | 842 | Rewrite | One loop; compaction before request; tools; termination |
| `results.py` | 54 | Adapt/simplify | Keep termination reasons and metrics |

`request.py` has 47 internal imports; `agent_loop.py` has 45. They are wiring
hubs. That is the refactor.

### `harness/session/` — rewrite around the new log

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `store.py` | 1,096 | Split | Keep `read_session_entries` validation, `build_session_branch`, and `_read_log_tail` recovery ideas; rewrite `SessionStore` metadata, fork, rewind, and index |
| `recorder.py` | 284 | Rewrite | Records `final` and `assistant` twice and stores surface replacements; Mini's writer appends facts only |
| `surface.py` | 477 | Rewrite | `project_session_surface` is the stored-projection problem; reuse/copy `validate_tool_pairing` |
| `replay.py` | 103 | Rewrite | Becomes a thin `project` call |
| `event_codec.py` | 148 | Rewrite | New schema and one event vocabulary |
| `artifacts.py` | 140 | Reuse/copy | Clean blob, preview, and resolver; fits the reference rule |
| `history.py` | 527 | Defer | Add a history tool after compaction; the search and exact-read logic is useful |
| `inbox.py` | 239 | Defer/simplify | One input per turn first; steering later |
| `types.py` | 46 | Adapt | Simple session types |
| `subagent_runs.py` | 53 | Defer | Arrives with subagents |
| `schema.py` | 8 | Replace | Mini's schema version |

The low-level store primitives are good. The class around them is the problem:
it mixes log storage, metadata, index, fork, rewind, and inbox repair.

### `harness/agent_runtime/` — rewrite the runtime, defer the features

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `harness.py` | 766 | Rewrite | Composition, gate, inbox, and hook wiring are the back-edge |
| `events.py` | 509 | Rewrite/delete | Translation layer between `AgentEvent` and `AgentHarnessEvent`; Mini has one event type |
| `config.py` | 560 | Rewrite/simplify | 69 imports; too many hooks and product policy |
| `composition.py` | 137 | Delete/defer | Mini requires an idle agent and a new provider for the next run |
| `tool_gate.py` | 788 | Defer/simplify | First increment uses a small policy |
| `run_control.py` | 226 | Defer | Steering and queuing later |
| `goal.py` | 293 | Defer | Goal judge later |
| `subagents.py` | 583 | Defer | Separate logs later |
| `context_window.py` | 281 | Rewrite | Replaced by compaction and window |
| `message_codec.py` | 125 | Defer | Run-state serialization later |
| `prompting/` | 306 | Adapt | Keep the identity and runtime context idea; drop section machinery |
| `result.py` | 151 | Adapt/simplify | Keep the result and termination shape |
| `cancellation.py`, `async_worker.py` | 111 | Reuse/adapt | Small and generic |
| `agent_helpers.py` | 187 | Adapt | Sync/async bridge is useful for the CLI |
| `tool_hooks.py`, `tool_audit.py` | 152 | Defer | Arrives with hooks and audit |

### `harness/security/` — defer, keep the vocabulary

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `permissions.py` | 812 | Defer | Full engine; first increment needs path boundary plus allow/deny/ask |
| `permission_model/` | 1,369 | Defer | `PermissionDecision`, `Rule`, `Action`, and `Target` are a useful vocabulary |
| `shell_analyzer.py` | 853 | Defer | Do not replace with regex; ask for shell or allow a small read-only list |
| `approval_reviewer.py` | 358 | Defer | No LLM approval in the first increment |
| `approval.py`, `rule_matcher.py` | 233 | Adapt small pieces | Types and first-match rules if needed |

The first-increment policy should be about 150 lines: resolve paths, deny escapes
and sensitive paths, allow reads, ask for writes and shell, and let `--yes` or
`--read-only` decide the host behavior.

### `harness/execution_env/` — reuse, defer the sandbox

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `filesystem.py` | 63 | Reuse | `FileSystem` and `LocalFileSystem` |
| `shell.py`, `subprocess.py`, `result.py` | 136 | Reuse | Shell protocol, subprocess runner, result |
| `_process.py`, `output_capture.py` | 134 | Reuse | Process lifecycle and bounded output |
| `sandbox.py` | 53 | Adapt | No-op sandbox first |
| `linux_sandbox.py` | 362 | Defer | Linux bubblewrap is a later policy layer |

This package imports no other `cade` package. It is the second clean leaf after
`ai/`.

### `coding_agent/tools/` — adapt six, defer the rest

| Module | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `read.py` | 314 | Adapt | Keep binary detection, pagination, directory listing, and truncation; drop render intents or keep as optional metadata |
| `write.py` | 92 | Adapt | Keep the write logic; drop render intent |
| `edit.py` | 160 | Adapt | Keep exact-match and replace logic; drop diff intent |
| `glob_search.py` | 403 | Adapt | Keep ripgrep-first search and gitignore handling; drop `ToolSpec` wrapper |
| `grep_search.py` | 352 | Adapt | Same |
| `bash.py` | 253 | Adapt | Keep shell argv, environment, timeout, and output truncation; drop `purpose=validation` and render intents |
| `path_utils.py` | 133 | Reuse/copy | Path boundary and binary detection |
| `truncate.py` | 82 | Reuse/copy | Tail truncation |
| `shell_adapter.py` | 190 | Reuse/copy | Shell detection |
| `_search_utils.py` | 339 | Adapt | Shared search helpers |
| `utils.py` | 87 | Adapt | Text I/O helpers |
| `text_edit.py` | 129 | Adapt | Pure edit helper |
| `file_image.py` | 53 | Defer | Image reading is optional |
| `file_index.py`, `file_mutation_queue.py` | 120 | Defer | Not needed for the six tools |
| `question.py`, `webfetch.py`, `websearch.py` | 534 | Defer | Later tools |
| `subagent.py` | 461 | Defer | Arrives with subagents |
| `tools_manager.py` | 37 | Adapt | External tool detection such as `rg` |

The tools import `cade.agent.types` and `cade.harness.execution_env`. Copy the
pure functions into Mini and keep the behavior; do not import the old builders.

### Product and hosts

| Area | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `coding_agent/app.py` | 472 | Rewrite | Mini's composition root is small |
| `coding_agent/harness.py` | 421 | Rewrite | A thin runtime; modes and skills later |
| `coding_agent/assembly/` | 922 | Rewrite/simplify | A small `build_app` replaces seven assembly modules |
| `execution_modes.py` | 234 | Defer | Keep mode names and notices as reference |
| `registry.py` | 65 | Adapt | Six tools |
| `validation.py` | 198 | Defer | Workspace fingerprint is a product feature |
| `working_note.py` | 70 | Defer | NOTE handoff later |
| `prompting/` | 133 | Adapt | Keep the core identity prompt |
| `interaction/` | 2,634 | Defer | Commands, completion, and settings are host services |
| `cli/` | 570 | Defer/adapt | Session CLI later; reuse exec flag names |
| `modes/exec_mode.py` | 807 | Defer/adapt | JSONL event protocol is useful; rewrite on Mini's event stream |
| `modes/tui/` | 7,791 | Defer | Host later |
| `server/` | 1,199 | Defer | Host later |
| `main.py` | 454 | Adapt | Small CLI; reuse flag names and the exec contract |

The host code is not wrong. It is attached to the wrong center. It should render
Mini's events and call Mini's run API, not own session and request logic.

### Extensions

| Area | Lines | Verdict | Note |
| --- | ---: | --- | --- |
| `harness/mcp/` | 1,903 | Defer | Add as a tool seam |
| `harness/skills/` | 1,038 | Defer | Add as a context and tool seam |
| `harness/memory/` | 363 | Defer | Add as a tool and context seam |
| `harness/observability/` | 705 | Defer | Keep a simple audit later |
| `harness/auth/` | 191 | Reuse if needed | Credential storage for providers |
| `harness/snapshot.py` | 451 | Defer | Self-contained; portable later |
| `harness/skill_activation.py` | 51 | Defer | Arrives with skills |

## Cross-cutting findings

1. **The hubs are the problem.** `agent_runtime/config.py` has 69 internal
   imports, `agent_runtime/harness.py` 79, `tool_gate.py` 53, `request.py` 47,
   and `agent_loop.py` 45. These modules combine wiring, policy, and runtime
   state. Rewriting them is the refactor.
2. **The lower layers are clean.** `ai/` and `harness/execution_env/` import no
   higher layer. The import firewall is viable.
3. **The tools are coupled to the old types.** They import `agent.types` and
   `harness.execution_env`. Port the logic, not the builders.
4. **The session store has good primitives under a heavy class.** Keep JSONL
   validation, branch walking, and tail recovery; rewrite metadata, fork, rewind,
   and index.
5. **`artifacts.py` is a reuse candidate.** It is small, bounded, and fits the
   blob-plus-reference rule. This corrects the earlier "defer" note.
6. **`snapshot.py` is portable but product-level.** It is self-contained and can
   be lifted later, but it is not pipeline.
7. **The permission engine is a product in itself.** Do not port it into the
   first increment; keep the vocabulary and write a small policy.
8. **The Responses adapter is justified complexity.** Wrap it; do not rewrite it
   for the first increment.
9. **The tests are contracts.** Harvest the replay, pairing, request, and tool
   behavior cases as Mini tests; do not try to keep the old module fixtures.

## Import firewall

Allowed:

- Standard library.
- `cade.ai`: provider protocol, adapters, auth, model registry.
- `cade.harness.execution_env`: filesystem, shell, result, process, output
  capture. Not `linux_sandbox`.

Forbidden: `cade.agent`, `cade.coding_agent`, and every other `cade.harness`
package.

Add an AST test under `src/cade/mini/tests/` that parses Mini modules and rejects
forbidden imports. The test is the boundary; review alone will drift.

## Snapshot undo

What it is: before and after each user turn, Cade writes a Git tree of the
workspace into a hidden repository under `.cade/snapshots/<session>/.git`. It
stores the changed-file list in an index. `/undo` checks each file for conflicts,
restores the pre-turn content, and reports per-file results.

Why defer: it is a host/product safety feature, not pipeline. It adds per-turn
Git work, an index, conflict semantics, and write permissions. Git already gives
the user `git diff`, `git checkout`, and `git stash`.

If wanted later: `harness/snapshot.py` is self-contained (451 lines, no `cade`
imports) and can be ported almost verbatim. The first increment can record a
workspace fingerprint or print `git status` at turn end.

Recommendation: defer. Revisit after the first increment if Git is not enough.

## First harvest order

1. Freeze the Mini event schema and compaction record. The decision is
   [Session schema](../reference/session-schema.md).
2. Copy or adapt the small pure helpers: `_tool_pairing.py`,
   `validate_tool_pairing`, `artifacts.py`, `path_utils.py`, `truncate.py`, and
   `shell_adapter.py`.
3. Adapt the six tool functions and write Mini `ToolSpec` wrappers.
4. Rewrite log, projection, and request.
5. Wrap the Codex provider and test `provider_state` round-trip.
6. Rewrite the loop and runtime.
7. Defer everything else.

## Risks

- `responses.py` may hide assumptions. Add a recorded-payload contract test
  before relying on it.
- Tool behavior ported without tests will regress: binary detection, truncation,
  path boundary, line endings.
- The new schema drops old sessions. Confirm no migration is required.
- Compaction cut points need real-session tests. Synthetic token counts do not
  prove provider behavior.
- The firewall test must confirm that `cade.ai` does not re-export a higher
  layer. Today it does not.
