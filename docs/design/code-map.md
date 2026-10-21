# Code map

Cade is an independent project. The `cade` distribution installs the
`cade` package and the `cade` command. It does not install or depend
on the earlier Cade project.

## Package layout

```text
src/cade/
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

Provider and execution modules originated in the earlier Cade project and are
now owned and maintained here. Cade includes only the process execution code it
uses, not the earlier project's filesystem or Linux sandbox implementation. Its
own process wrapper uses installed bubblewrap on Linux and Seatbelt on macOS;
Windows runs directly.

## Entry points

```bash
uv run --locked cade
uv run --locked cade exec "Fix the failing test."
uv run --locked python -m cade
uv run --locked cade login
```

The console script and Python module call `cade.hosts.cli:main`. Login
is handled by `cade.hosts.auth`. Credentials and user skills live under
`~/.cade/`. Project sessions and other state live under `.cade/`.
State from the earlier Cade project is not compatible.

## Import direction

- `ai` and `execution_env` do not import application or host modules.
- `core` imports core types and AI protocols.
- `tools`, `safety`, and `extensions` connect actions and policies to the core.
- `app` owns startup settings, credential persistence, and request context.
- `hosts` assemble these components and own user interaction.

The package root has no public re-exports; import the owning submodule
directly.

See [Architecture](../architecture.md),
[How Cade works](how-it-works.md), and
[Capability seams](capability-seams.md) for runtime contracts.
