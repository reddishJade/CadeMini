# Code map

Cade is an independent project. The `cade` distribution installs the
`cade` package and the `cade` command. It does not install or depend
on the earlier Cade project.

## Package layout

```text
src/cade/
  ai/                       providers, model protocols, authentication
  harness/
    session/                facts, schema, log, projection, history
    execution/              scope contracts and local execution backends
    loop.py                 log-driven turns and provider retries
    executor.py             shared tool authorization and execution
    tools.py, provider.py   runtime contracts
    request.py              request admission and wire conversion
    compaction.py           summary-based context reduction
    window.py               explicit context window reset
  coding/
    tools/                  read, write, edit, search, bash, history, question
    context/                prompts, instructions, skills, note, memory
    security/               workspace policy, rules, grants, approval, review
    workspace/              snapshots and coordinated recovery
    subagents.py            coding child sessions and delegation tools
    mcp.py, mcp_config.py    MCP tools and connection configuration
    goal.py                 workspace goal state and tool
  app/                      bootstrap, active session, config, auth, commands
  hosts/                    CLI and TUI presentation
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

- `ai` imports no other Cade layer.
- `harness` imports only itself and `ai`; local execution is internal to it.
- `coding` imports itself, `harness`, and `ai`.
- `app` assembles these lower layers and owns product-level session operations.
- `hosts` call application operations and adapt lower-layer contracts for I/O.

`app.bootstrap.create_session()` is the shared assembly entry point. It accepts
configuration and caller-owned resources without importing terminal modules.
`ApplicationSession.run_task()` serves both one-shot and TUI execution.
`harness.executor.execute_tool()` is the common tool execution boundary.

Configuration types belong to the capability they configure: permission rules
live in `coding/security/rules.py`, and MCP connection settings live in
`coding/mcp_config.py`. `CadeConfig` composes them, so capabilities do not import
application configuration.

`tests/test_architecture.py` enforces package dependencies and execution-contract
isolation. There are no legacy `core`, `execution_env`, `extensions`, `safety`,
or `tools` packages at the Cade root.

The package root has no public re-exports; import the owning submodule
directly.

See [Architecture](../architecture.md),
[How Cade works](how-it-works.md), and
[Capability seams](capability-seams.md) for runtime contracts.
