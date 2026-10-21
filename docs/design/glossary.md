# Glossary

One canonical term per concept. Links point to the owning page.

## session log

The append-only JSONL file that records facts. It is the source of truth for the
active branch. See [Log](../subsystems/log.md).

## branch

A path through the entry tree. The active branch ends at the latest entry and
supplies the projection.

## projection

The deterministic conversation produced from a branch. It applies the latest
system, compaction, and window cuts, then pairs tool calls with results. See
[Projection and request](../subsystems/projection-request.md).

## request

The admitted, provider-ready form of a conversation for one model call. It
includes tools, budget, and request-only context. A request is not a fact and is
never projected.

## run

One invocation of the loop over a task. A run contains one or more steps and
ends with a `run` fact.

## step

One model request plus the tool calls caused by its response.

## tool result

The recorded output of one tool call, paired by call ID. It is a fact.

## compaction

A fact that adds a summary and a `first_kept_id`. It shadows older messages in
later projections without deleting them.

## window

A fact that moves the context start forward, optionally with a handoff. It is a
fallback when compaction cannot fit.

## history

The tool that retrieves exact evidence by entry ID or call ID after compaction
or pruning.

## mode

`plan`, `build`, or `ask`. A mode selects the default policy for tools and shell
commands.

## rule

A static policy entry in config. Rules can match tool, path prefix, and command
prefix and return allow, deny, or ask.

## grant

A process-local approval created by answering `a`. It matches the same tool and
path or command.

## skill

A `SKILL.md` body loaded on demand. The request carries a catalog; the `skill`
tool loads the body.

## MCP

Model Context Protocol. Cade runs stdio MCP servers and exposes their tools
as `mcp__<server>__<tool>`.

## subagent

A child run with its own session and a restricted tool set. It cannot spawn
another subagent.

## snapshot

A pre-change copy of a file, used by the one-shot `--undo` option.

## goal

A durable objective in `.cade/goal.json`, separate from the session
schema.
