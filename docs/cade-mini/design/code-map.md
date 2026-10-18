# Code map and relationship to Cade

## Where Cade Mini lives

Cade Mini is the `cade.mini` subpackage in the `cade-agent` distribution:

```text
src/cade/
  ai/                    shared provider adapters, events, auth, models
  harness/               shared execution environment, sessions, policies
  agent/                 Cade's own agent loop
  coding_agent/          Cade's product assembly, TUI, tools, modes
  server/                Cade's browser server
  mini/                  Cade Mini (this package)
```

Cade Mini is not a copied-out project. It is a parallel implementation that
shares only two lower layers with Cade:

- `cade.ai`: provider streaming, model resolution, credentials, usage.
- `cade.harness.execution_env`: subprocess execution.

There is no OS sandbox in Mini. `test_mini_boundary.py` enforces that Mini does
not import `cade.agent`, `cade.harness` session/policy modules,
`cade.coding_agent`, or `cade.server`. Mini owns its own log, projection, request
builder, loop, tools, policy, and hosts.

## Can it start on its own?

Yes, at runtime:

```bash
uv run --locked cade-mini
uv run --locked cade-mini "Fix the failing test."
uv run --locked python -m cade.mini
uv run --locked cade-mini --web --port 8765
```

The `cade-mini` console script and `python -m cade.mini` both call
`cade.mini.hosts.cli:main`. The terminal, web, sessions, snapshots, goals, and
extensions are Mini code.

It is **not** a standalone distribution:

- Installing `cade-agent` installs both `cade` and `cade-mini`.
- Mini still imports `cade.ai` and `cade.harness.execution_env`.
- Credentials are Cade's `~/.cade/auth.json`; there is no separate Mini login.
- Copying only `src/cade/mini/` will not run without the `cade` package.

So "standalone" means a separate entry point and runtime surface, not a separate
wheel or repository.

## Module layout

The package is grouped by layer. `cade.mini` has no public re-exports and no
`cade.mini.cli` compatibility module; import the owning submodule directly.

```text
src/cade/mini/
  core/        types, log, projection, request, loop, compaction, window
  tools/       workspace, history
  safety/      policy, shell, grants, approval, review
  extensions/  skills, memory, hooks, mcp, subagents, snapshots, goal
  hosts/       cli, web
  app/         config, provider, auth, prompts, instructions, note
  docs.py
  __main__.py
```

| Group | Approx. lines |
| --- | ---: |
| Core pipeline | 1,741 |
| Tools and history | 615 |
| Safety | 415 |
| Extensions | 966 |
| Hosts and docs | 1,957 |
| App plumbing | 353 |

The largest files are `hosts/cli.py`, `docs.py`, `core/loop.py`, and
`tools/workspace.py`.

## Import direction

- `core` imports only `core` and `cade.ai`.
- `tools` imports `core`, `safety.shell`, and `extensions.snapshots`.
- `safety` imports `core`, `app.config`, and `tools.workspace`.
- `extensions` imports `core`, `app`, `tools`, and `safety`.
- `hosts` imports every layer; nothing imports `hosts`.
- `app` imports provider/auth only; it does not import the loop.

The entry point is `hosts.cli`, not a compatibility module. Tests import the
owning module, for example `cade.mini.core.log.SessionLog` or
`cade.mini.tools.workspace.build_tools`.

## Reading order

Start with [How Cade Mini works](how-it-works.md), then
[Architecture](../architecture.md), then [Capability
seams](capability-seams.md). The [subsystems](../subsystems/README.md) pages are
the implementation reference. [Cade comparison](cade-comparison.md) explains
what Mini intentionally does not include.
