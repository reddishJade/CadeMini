# Cade Mini architecture

Cade Mini is a standalone coding agent. All runtime code lives in
`src/cade_mini/`; it does not import or install Cade.

The main pipeline separates durable facts from model requests:

```text
Session log -> Conversation projection -> Request admission -> Run
```

- `core/` owns logs, projection, budgets, the loop, compaction, and window changes.
- `ai/` owns provider protocols, streaming adapters, model metadata, and OAuth.
- `execution_env/` owns local process execution and output capture.
- `tools/`, `safety/`, and `extensions/` implement actions, permissions, and add-ons.
- `app/` owns configuration, credentials, and request context.
- `hosts/` owns terminal, login, and web interaction.

Provider and process modules do not depend on the application or hosts. Tests
in `tests/test_mini_boundary.py` enforce independence from the old Cade package.
Mini has no OS sandbox; shell execution uses local host permissions.

Credentials and user skills use `~/.cade-mini/`. Sessions, snapshots, goals,
and project skills use `<workspace>/.cade-mini/`. Mini reads project settings
from `cade-mini.json`. It does not discover old Cade state.

See the [pipeline architecture](cade-mini/architecture.md),
[code map](cade-mini/design/code-map.md), and
[persistence catalog](cade-mini/design/persistence-catalog.md) for contracts
and implementation locations.
