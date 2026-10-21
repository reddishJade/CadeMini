# Cade package

`cade` is the Python package in the standalone `cade` distribution.
It owns all runtime code and does not depend on Cade.

## Entry points

```bash
cade
python -m cade
cade login
```

`cade.hosts.cli:main` is the console script target. Import the owning
submodule directly; the package root has no public re-exports.

## Package layout

```text
cade/
  ai/             model protocols, providers, authentication
  harness/        log-driven agent runtime and internal execution backends
  coding/         programming tools, context, security, workspace recovery
  app/            configuration, assembly, active sessions, commands
  hosts/          CLI and TUI presentation
  docs.py
  __main__.py
```

See [Architecture](../../docs/architecture.md) and the
[code map](../../docs/design/code-map.md).
