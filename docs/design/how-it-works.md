# How Cade works

Cade coordinates one model request at a time, executes the requested tools,
and records every durable fact in a session log.

## One run

A submitted task is appended as a `user` fact. Cade projects the active
branch, builds a request for the current budget, and sends it to the provider.
The provider streams text and tool calls. The loop records the assistant
message, executes each tool call, and records a tool result. If the model needs
another request, the loop starts another step; otherwise the run ends.

A step is one request plus the tools caused by its response. A run can contain
many steps. The loop stops on completion, a step limit, context exhaustion,
cancellation, or a provider error.

## Context

The projection supplies conversation history. Request-only context supplies
project instructions, the skill catalog, `NOTE.md`, `MEMORY.md`, and an active
goal. Request admission preserves projected tool results and rejects requests
that exceed the budget. The history tool can read original retained facts.

Compaction adds a summary fact and keeps a recent tail. A window moves the
context start forward and may include a handoff. Neither operation rewrites the
original messages.

## Sessions

Sessions are JSONL files under `.cade/sessions`. Entries have IDs and parent
IDs, so a session can form a tree; the active branch ends at the latest entry.
Resuming opens the same file and appends new facts.

## Interfaces

The terminal host runs one task or a TUI. These interfaces call the same loop
and share policy, tools, and session storage with every other entry path.

## Extensions

Skills add on-demand instructions. Memory adds durable facts. MCP adds remote
tools. Subagents run an independent child session. Each extension is a narrow
adapter over the loop, not a change to the session schema.

## Trust and permissions

Every tool call passes through a policy. Modes, static rules, session grants,
shell classification, and approvals decide whether a call runs. Linux/macOS
shell and file tools also use OS confinement; Windows runs directly. See
[Tools and security](../tools-security.md) for scopes and limitations.

See [Architecture](../architecture.md) for the invariant and
[Capability seams](capability-seams.md) for the contracts.
