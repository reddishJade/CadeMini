# Cade Mini package

Cade Mini is a parallel implementation inside the `cade-agent` distribution. It
shares only `cade.ai` (providers, auth, models) and
`cade.harness.execution_env` (subprocess execution) with Cade. Everything else
here is independent.

## Entry points

```bash
cade-mini
python -m cade.mini
cade-mini --web --port 8765
```

`cade.mini.hosts.cli:main` is the console script target. There is no
`cade.mini.cli` compatibility module and `cade.mini.__init__` has no public
re-exports; import the owning submodule directly.

## Package layout

```text
mini/
  core/        types, log, projection, request, loop, compaction, window
  tools/       workspace, history
  safety/      policy, shell, grants, approval, review
  extensions/  skills, memory, hooks, mcp, subagents, snapshots, goal
  hosts/       cli, web
  app/         config, provider, auth, prompts, instructions, note
  docs.py
  __main__.py
```

Read [the code map](../../../docs/cade-mini/design/code-map.md) for the layer
view and [the comparison with
Cade](../../../docs/cade-mini/design/cade-comparison.md) for the capability gap.
