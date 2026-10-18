# Cade Mini

Cade Mini is the small coding agent in the Cade repository. It keeps one rule at
the center of the design:

> The session log is the source of truth. The model-visible history is a
> deterministic projection. Request construction only produces the current
> request.

The result is a small agent that can survive compaction, window changes, and
process restarts without inventing history.

## Start here

- [Quickstart](quickstart.md) installs and runs the first task.
- [CLI](cli.md) lists one-shot, REPL, session, web, and recovery commands.
- [Configuration](configuration.md) documents `cade-mini.json`.
- [Architecture](architecture.md) explains the log, projection, request, and run
  pipeline.
- [Sessions and context](sessions-context.md) covers resume, compaction,
  pruning, and history retrieval.
- [Tools and security](tools-security.md) covers modes, rules, grants, and shell
  analysis.
- [Snapshots and goals](snapshots-goals.md) covers undo and durable objectives.
- [Skills and memory](skills-memory.md) covers `SKILL.md` and `MEMORY.md`.
- [Hooks, MCP, and subagents](hooks-mcp-subagents.md) covers extensions.
- [Hosts](hosts.md) covers the CLI REPL and the local web host.
- [Development](development.md) covers checks, tests, and the docs pipeline.
- [Parity](parity.md) links the acceptance against Cade.

## Design

- [How Cade Mini works](design/how-it-works.md) is the short end-to-end model.
- [Code map and relationship to Cade](design/code-map.md) explains the package
  boundary and the entry points.
- [Cade comparison](design/cade-comparison.md) gives scale and capability gaps.
- [Capability seams](design/capability-seams.md) lists the replaceable
  contracts.
- [Event model](design/event-model.md) separates facts from audits.
- [Tool execution](design/tool-execution.md) is the policy-to-hook pipeline.
- [Configuration catalog](design/config-catalog.md) and
  [event catalog](design/event-catalog.md) are generated references.
- [Tool catalog](design/tool-catalog.md) lists the built-in tools.
- [MCP reference servers](design/mcp-reference.md) lists the supported server
  pattern.
- [Steering and queueing](design/steering-queue.md) explains the concurrent
  REPL host.
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

The guides are task-oriented. The detailed design and schema references remain
the source of truth for invariants:

- [Cade Mini design](../cade-mini.md)
- [Session log schema](../cade-mini-schema.md)
- [Harvest notes](../cade-mini-harvest.md)
- [Parity acceptance](../cade-mini-parity.md)

## Built docs

The docs are Markdown-first. A small tool validates links and anchors, builds a
single-page site, and generates an `llms.txt` catalog for agents:

```bash
python -m cade.mini.docs check
python -m cade.mini.docs build
python -m cade.mini.docs llms --write
python -m cade.mini.docs catalogs --write
```

The generated site is [docs/mini/index.html](../mini/index.html); the generated
catalogs are `docs/cade-mini/llms.txt`, `docs/cade-mini/llms-full.txt`, and the
config, event, and module pages under `design/`.
