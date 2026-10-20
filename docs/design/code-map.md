# Code map

Cade Mini is an independent project. The `cade-mini` distribution installs the
`cade_mini` package and the `cade-mini` command. It does not install or depend
on Cade.

## Package layout

```text
src/cade_mini/
  ai/             provider adapters, events, auth protocols, models
  execution_env/  local subprocess execution and output capture
  core/           log, projection, request, loop, compaction, window
  tools/          workspace actions and history retrieval
  safety/         policy, shell analysis, grants, approval, review
  extensions/     skills, memory, hooks, mcp, subagents, snapshots, goal
  app/            config, provider assembly, credentials, request context
  hosts/          cli, auth
  docs.py
  __main__.py
tests/            regression and integration tests
```

Provider and execution modules originated in Cade and are now owned and
maintained here. Mini includes only the process execution code it uses, not
Cade's filesystem or Linux sandbox implementation. Mini's own process wrapper
uses installed bubblewrap on Linux and Seatbelt on macOS; Windows runs directly.

## Entry points

```bash
uv run --locked cade-mini
uv run --locked cade-mini exec "Fix the failing test."
uv run --locked python -m cade_mini
uv run --locked cade-mini login
```

The console script and Python module call `cade_mini.hosts.cli:main`. Login
is handled by `cade_mini.hosts.auth`. Credentials and user skills live under
`~/.cade-mini/`. Project sessions and other state live under `.cade-mini/`.
No old Cade directories or commands are used.

## Import direction

- `ai` and `execution_env` do not import application or host modules.
- `core` imports core types and AI protocols.
- `tools`, `safety`, and `extensions` connect actions and policies to the core.
- `app` owns startup settings, credential persistence, and request context.
- `hosts` assemble these components and own user interaction.

`tests/test_mini_boundary.py` rejects imports from the old `cade` namespace
and application imports in the lower layers. The package root has no public
re-exports; import the owning submodule directly.

See [Architecture](../architecture.md),
[How Cade Mini works](how-it-works.md), and
[Capability seams](capability-seams.md) for runtime contracts.
