# Cade Mini package

`cade_mini` is the Python package in the standalone `cade-mini` distribution.
It owns all runtime code and does not depend on Cade.

## Entry points

```bash
cade-mini
python -m cade_mini
cade-mini login
cade-mini --web --port 8765
```

`cade_mini.hosts.cli:main` is the console script target. Import the owning
submodule directly; the package root has no public re-exports.

## Package layout

```text
cade_mini/
  ai/             provider adapters, auth protocols, models, events
  execution_env/  subprocess execution and output capture
  core/           types, log, projection, request, loop, compaction, window
  tools/          workspace, history
  safety/         policy, shell, grants, approval, review
  extensions/     skills, memory, hooks, mcp, subagents, snapshots, goal
  app/            config, provider, credential storage, prompts, instructions, note
  hosts/          cli, auth, web
  docs.py
  __main__.py
```

See [Architecture](../../docs/architecture.md) and the
[code map](../../docs/design/code-map.md).
