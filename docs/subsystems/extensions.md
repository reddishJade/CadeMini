# Extensions

Extensions add behavior through narrow seams. None changes the session schema.

## Skills

Discovery reads skill metadata from user and trusted project roots. The request
carries a catalog; the `skill` tool loads a body or reference on demand and
returns resource paths. Activated instructions missing after compaction or a
window reset are restored from the active branch into request context. See
[Skills and memory](../skills-memory.md) for configuration and activation.

## Memory

`MEMORY.md` is request context. The `memory` tool appends one bullet and cannot
write other paths. The file is capped to protect the request budget.

## MCP

Each configured stdio or HTTP server keeps one connection. Discovery reads
`tools/list`; calls use `tools/call` over a serialized queue. Tools are prefixed
`mcp__`, ask by default, and are denied in plan and read-only modes. A server
that cannot start is reported and skipped.

## Subagents

`SubagentTool` creates a child session under `<sessions>/subagents`. The child
gets the parent provider, budget, policy, approver, context, and base tools plus
its own history. It cannot spawn another subagent and is limited to
20 steps. The parent records only the call and summary. `DelegateTool` runs one
or more child tasks sequentially and optionally applies a more restrictive
child mode. Reviewer requests retain ancestor conversations so a child prompt
cannot replace human restrictions.

## Snapshots

`SnapshotStore` captures `write`, `edit`, and common single-command shell
mutations. `WorkspaceRecovery` coordinates checkpoint actions for CLI `--undo`
and TUI `/undo` and `/redo`. Snapshots are project state, not session facts.

## Goals

`GoalStore` persists one objective in `.cade/goal.json`. The `goal` tool and
`/goal` command manage it. An active goal is request context.

## Hosts

The terminal host runs one-shot tasks and the TUI. It uses the same loop and
policy as every other entry path.

## Limits

MCP capability and protocol boundaries are documented in
[MCP](../mcp.md). See [Subagents](../subagents.md) for child-session permissions
and sequential execution.

## Tests

`test_skills.py`, `test_memory.py`, `test_mcp.py`, `test_mcp_integration.py`, `test_subagents.py`,
`test_snapshots.py`, and `test_goal.py`.
