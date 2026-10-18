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
- [Tools and security](tools-security.md) covers modes, rules, grants, shell
  analysis, and the sandbox.
- [Snapshots and goals](snapshots-goals.md) covers undo and durable objectives.
- [Skills and memory](skills-memory.md) covers `SKILL.md` and `MEMORY.md`.
- [Hooks, MCP, and subagents](hooks-mcp-subagents.md) covers extensions.
- [Hosts](hosts.md) covers the CLI REPL and the local web host.
- [Development](development.md) covers checks, tests, and the docs pipeline.
- [Parity](parity.md) links the acceptance against Cade.

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
```

The generated site is [docs/mini/index.html](../mini/index.html); the generated
catalog is `docs/cade-mini/llms.txt`.
