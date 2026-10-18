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
- `cade.harness.execution_env`: subprocess execution and the optional
  bubblewrap sandbox.

`test_mini_boundary.py` enforces that Mini does not import `cade.agent`,
`cade.harness` session/policy modules, `cade.coding_agent`, or `cade.server`.
Mini owns its own log, projection, request builder, loop, tools, policy, and
hosts.

## Can it start on its own?

Yes, at runtime:

```bash
uv run --locked cade-mini
uv run --locked cade-mini "Fix the failing test."
uv run --locked python -m cade.mini
uv run --locked cade-mini --web --port 8765
```

The `cade-mini` console script and `python -m cade.mini` both call
`cade.mini.cli:main`. The terminal, web, sessions, snapshots, goals, and
extensions are Mini code.

It is **not** a standalone distribution:

- Installing `cade-agent` installs both `cade` and `cade-mini`.
- Mini still imports `cade.ai` and `cade.harness.execution_env`.
- Credentials are Cade's `~/.cade/auth.json`; there is no separate Mini login.
- Copying only `src/cade/mini/` will not run without the `cade` package.

So "standalone" means a separate entry point and runtime surface, not a separate
wheel or repository.

## Current module layout

The package is deliberately flat. Each module owns one responsibility, but the
layer is not visible in the filename.

| Group | Modules | Approx. lines |
| --- | --- | ---: |
| Core pipeline | `types`, `log`, `projection`, `request`, `loop`, `compaction`, `window` | 1,680 |
| Workspace tools | `tools`, `history` | 617 |
| Safety | `policy`, `shell`, `grants`, `approval`, `review`, `sandbox` | 444 |
| Extensions | `skills`, `memory`, `hooks`, `mcp`, `subagents`, `snapshots`, `goal` | 852 |
| Hosts and docs | `cli`, `web`, `docs` | 1,493 |
| App plumbing | `config`, `provider`, `auth`, `prompts`, `instructions`, `note` | 353 |
| Entry | `__init__`, `__main__` | 146 |

The largest files are `docs.py` (27 KB), `cli.py` (22 KB), `loop.py` (19 KB), and
`tools.py` (18 KB). The core pipeline is about 1,700 lines.

## Why it is flat

- The package is small enough to inspect in one directory.
- Cross-layer imports are easy to see and the import firewall is explicit.
- Moving modules changes public import paths, the console script target, and
  tests. That should be a dedicated refactor, not a side effect of a feature
  change.

The cost is that a reader cannot tell core from extensions without opening
files. A documented map, not a filesystem move, fixes that for now.

## Target layout if we group it

```text
src/cade/mini/
  core/        types, log, projection, request, loop, compaction, window
  tools/       tools, history
  safety/      policy, shell, grants, approval, review, sandbox
  extensions/  skills, memory, hooks, mcp, subagents, snapshots, goal
  hosts/       cli, web
  app/         config, provider, auth, prompts, instructions, note
  docs.py
```

A pure move would keep `cade.mini.__init__` re-exports and a `cade.mini.cli`
compatibility module so `cade-mini` and existing imports keep working. It would
update relative imports, tests that import implementation modules directly, and
the entry point. Behavior would not change.

## Reading order

Start with [How Cade Mini works](how-it-works.md), then
[Architecture](../architecture.md), then [Capability
seams](capability-seams.md). The [subsystems](../subsystems/README.md) pages are
the implementation reference. [Cade comparison](cade-comparison.md) explains
what Mini intentionally does not include.
