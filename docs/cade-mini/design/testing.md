# Testing

Tests are grouped by the layer they protect. Run the smallest group that covers
a change, then the full suite before a release.

## Commands

```bash
uv run --locked pytest -q src/cade/tests
uv run --locked pytest -q src/cade/tests/test_mini_loop.py
uv run --locked ruff check src/cade/
uv run --locked ruff format --check src/cade/
uv run --locked pyright src/cade/
uv run --locked python -m cade.mini.docs check
```

## Coverage map

| Area | Tests |
| --- | --- |
| Log, tail repair, sessions | `test_mini_log.py` |
| Projection and pairing | `test_mini_projection.py` |
| Budget, pruning, wire format | `test_mini_request.py` |
| Loop, approvals, retries, windows | `test_mini_loop.py` |
| Provider and auth | `test_mini_provider.py` |
| Workspace tools | `test_mini_tools.py` |
| Compaction and windows | `test_mini_compaction.py`, `test_mini_window.py` |
| CLI and config | `test_mini_cli.py`, `test_mini_config.py` |
| Instructions, skills, memory, NOTE | `test_mini_instructions.py`, `test_mini_skills.py`, `test_mini_memory.py`, `test_mini_note.py` |
| Safety and modes | `test_mini_policy.py`, `test_mini_shell.py`, `test_mini_grants.py`, `test_mini_sandbox.py` |
| Extensions | `test_mini_hooks.py`, `test_mini_mcp.py`, `test_mini_subagents.py` |
| Recovery | `test_mini_snapshots.py`, `test_mini_goal.py` |
| Hosts | `test_mini_web.py` |
| Docs | `test_mini_docs.py` |
| Import firewall | `test_mini_boundary.py` |

## Real-provider checks

The acceptance runs use DeepSeek or Codex and are not part of the default test
suite. They cover tool use, resume, compaction, skills, build mode, the web
host, and Codex write/read. Record the command and observed result in the change
that adds a new acceptance.

## Docs checks

`test_mini_docs.py` and `python -m cade.mini.docs check` validate the manifest,
links, anchors, and generated catalogs. The docs job runs in CI.

## Known noise

The full suite has three pre-existing Windows-only failures in untouched Cade
modules: two working-directory assertions and one shell line-ending assertion.
Treat them as unrelated until that module is changed.
