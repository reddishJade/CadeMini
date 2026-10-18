# Extensions

Extensions add behavior through narrow seams. None changes the session schema.

## Skills

Discovery reads `SKILL.md` from user and project roots. The request carries a
catalog; the `skill` tool loads a body into a tool result. A body is a fact, not
request-only context.

## Memory

`MEMORY.md` is request context. The `memory` tool appends one bullet and cannot
write other paths. The file is capped to protect the request budget.

## Hooks

`HookRunner` executes configured shell commands around tools. `before_tool` can
deny; `after_tool` observes. Hooks are trusted and time-bounded. See
[Hooks, MCP, and subagents](../hooks-mcp-subagents.md).

## MCP

Each configured stdio server keeps one persistent process. Discovery reads
`tools/list`; calls use `tools/call` over a serialized queue. Tools are prefixed
`mcp__`, ask by default, and are denied in plan and read-only modes. A server
that cannot start is reported and skipped.

## Subagents

`SubagentTool` creates a child session under `<sessions>/subagents`. The child
gets the parent provider, budget, policy, approver, hooks, context, and base
tools plus its own history. It cannot spawn another subagent and is limited to
20 steps. The parent records only the call and summary.

## Snapshots

`SnapshotStore` captures `write`, `edit`, and common single-command shell
mutations. `--undo` and `/undo` restore the latest snapshot. Snapshots are project
state, not session facts.

## Goals

`GoalStore` persists one objective in `.cade-mini/goal.json`. The `goal` tool and
`/goal` command manage it. An active goal is request context.

## Hosts

The CLI runs one-shot and REPL sessions. The web host serves a page and a small
JSON API over one session. Both use the same loop and policy.

## Limits

Extensions are deliberately small. MCP is a stdio client, not a full capability
negotiation implementation. Hooks are shell commands, not in-process callbacks.
Subagents are sequential and share the parent approval context.

## Tests

`test_mini_skills.py`, `test_mini_memory.py`, `test_mini_hooks.py`,
`test_mini_mcp.py`, `test_mini_subagents.py`, `test_mini_snapshots.py`,
`test_mini_goal.py`, and `test_mini_web.py`.
