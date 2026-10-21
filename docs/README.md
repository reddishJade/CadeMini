# Cade

Cade is a standalone coding agent. It keeps one rule at the center of the
design:

> The session log is the source of truth. The model-visible history is a
> deterministic projection. Request construction only produces the current
> request.

The result is a small agent that can survive compaction, window changes, and
process restarts without inventing history.

Chinese translations of the current guides start at [中文文档](zh/README.md).

## Start here

- [Quickstart](quickstart.md) installs and runs the first task.
- [CLI](cli.md) lists one-shot, TUI, session, and recovery commands.
- [Configuration](configuration.md) documents `cade.json`.
- [Architecture](architecture.md) explains the log, projection, request, and run
  pipeline.
- [Sessions and context](sessions-context.md) covers resume, compaction,
  pruning, and history retrieval.
- [Tools and security](tools-security.md) covers modes, rules, grants, and shell
  analysis.
- [Snapshots and goals](snapshots-goals.md) covers undo and durable objectives.
- [Skills and memory](skills-memory.md) covers `SKILL.md` and `MEMORY.md`.
- [MCP](mcp.md) covers external tools, resources, and prompts.
- [Subagents](subagents.md) covers child sessions and delegation.
- [Hosts](hosts.md) covers the terminal TUI and one-shot execution.
- [Development](development.md) covers checks, tests, and the docs pipeline.
- [Parity](parity.md) links the acceptance against Cade.

## Design

- [How Cade works](design/how-it-works.md) is the short end-to-end model.
- [Code map](design/code-map.md) explains package boundaries and entry points.
- [Relationship to Cade](design/cade-comparison.md) explains independent ownership.
- [Capability seams](design/capability-seams.md) lists the replaceable
  contracts.
- [Event model](design/event-model.md) separates facts from audits.
- [Tool execution](design/tool-execution.md) is the policy-to-result pipeline.
- [Configuration catalog](design/config-catalog.md) and
  [event catalog](design/event-catalog.md) are generated references.
- [Tool catalog](design/tool-catalog.md) lists the built-in tools.
- [MCP reference servers](design/mcp-reference.md) lists the supported server
  pattern.
- [Steering and queueing](design/steering-queue.md) explains concurrent input
  in the TUI.
- [Module graph](design/module-graph.md) is generated from imports.
- [Persistence catalog](design/persistence-catalog.md) maps every artifact.
- [Glossary](design/glossary.md) defines the vocabulary.
- [Testing](design/testing.md) maps tests to layers.

## Subsystems

[Subsystems](subsystems/README.md) is the implementation reference for the log,
projection and request, loop, tools and policy, context and compaction, and
extensions.

## Cookbook

[Cookbook](cookbook/README.md) has procedures for fixing a bug, adding a
feature, review-only work, long sessions, and a custom MCP server.

## History

- [Postmortems](postmortem/README.md) record incidents and their fixes.
- [Persistence](persistence/README.md) describes the current format and the
  change policy.
- [Upgrade guide](upgrade/README.md) records breaking changes.
- [Decision records](decisions/README.md) record durable design choices.

## Design and reference

The [session schema](reference/session-schema.md) defines the persistence
contract. Historical research and acceptance records describe the project before
it became independent:

- [Cade design](history/design-proposal.md)
- [Session log schema](reference/session-schema.md)
- [Harvest notes](history/harvest-audit.md)
- [Parity acceptance](history/parity-acceptance.md)

## Built docs

The docs are Markdown-first. A small tool validates links and anchors, builds a
single-page site, and generates an `llms.txt` catalog for agents:

```bash
uv run --locked python -m cade.docs check
uv run --locked --extra docs python -m cade.docs build
uv run --locked python -m cade.docs llms --write
uv run --locked python -m cade.docs catalogs --write
```

The generated site is [docs/index.html](index.html); the generated
catalogs are `docs/llms.txt`, `docs/llms-full.txt`, and the
config, event, and module pages under `design/`.
