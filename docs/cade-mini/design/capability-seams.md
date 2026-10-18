# Capability seams

Cade Mini keeps a small number of explicit contracts. A seam is a place where an
implementation can be replaced without changing the session schema or the loop.

| Seam | Contract | Implementations | Main consumers |
| --- | --- | --- | --- |
| Provider | `loop.Provider` streaming events | Chat providers, Codex | Loop, compaction, auto-review |
| Tool | `loop.Tool` name, description, parameters, execute | Workspace tools, history, skill, memory, goal, subagent, MCP | Loop |
| Policy | `loop.Policy.check` returns allow, deny, or ask | `WorkspacePolicy` | Loop |
| Approval | Callable returning bool or awaitable bool | `SessionApprover`, `AutoReviewer` | Loop |
| Hooks | `loop.Hooks.before/after` | `HookRunner` | Loop |
| Compaction | `Compactor(log, budget)` | LLM summary compactor | Loop |
| Session store | `SessionLog` append, branch, open | File-backed JSONL log | Every layer above it |
| Recovery | `SnapshotStore` capture and undo | File snapshot store | Tools, CLI |
| Host | A task runner over the loop | CLI, web host | Users |

## Provider

The loop needs a streaming event source: text deltas, tool calls, a final
message, usage, and failures. A provider owns wire conversion and replay state.
Cade Mini does not depend on provider-specific message types above this seam.

## Tool

A tool declares a JSON schema and an async `execute`. A tool result is content,
an error flag, and optional metadata. Tools do not write the session log; the
loop records their results.

## Policy and approval

Policy returns a decision, not an execution. Approval is only consulted for
`ask`. A denial becomes a tool error fact, so the model can recover. Keeping
these separate lets the host choose a TTY prompt, a model reviewer, or a
non-interactive denial.

## Hooks

Hooks observe and can veto a tool call without changing the policy result. A
`before_tool` failure is a denial; `after_tool` failure is ignored. Hooks are
trusted configuration, not sandboxed code.

## Stores

`SessionLog` is the durable history. `SnapshotStore`, `GoalStore`, and the
memory and NOTE files are host state, not session facts. Treating them as
separate stores keeps the schema closed while allowing recovery and long-term
state.

## Host

A host owns presentation, input, and the event loop boundary. It does not own
the agent loop or the schema. The CLI and web host differ only in how they run a
task and render events.

## Tests as contracts

Each seam has a focused test module. `test_mini_boundary.py` enforces the import
firewall, so a seam cannot quietly become a dependency on the Cade product
layers.
