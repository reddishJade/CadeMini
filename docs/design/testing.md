# Testing

Tests are grouped by the layer they protect. Run the smallest group that covers
a change, then the full suite before a release.

## Commands

```bash
mkdir -p e2e-results
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
uv run --locked pytest -q tests/test_mini_loop.py --basetemp=e2e-results/pytest
uv run --locked ruff check src/cade_mini/ tests/
uv run --locked ruff format --check src/cade_mini/ tests/
uv run --locked pyright src/cade_mini/
uv run --locked python -m cade_mini.docs check
```

## Coverage map

| Area | Tests |
| --- | --- |
| Sessions | `test_mini_log.py` |
| Projection and pairing | `test_mini_projection.py` |
| Budget, pruning, wire format | `test_mini_request.py` |
| Loop, approvals, retries, windows | `test_mini_loop.py` |
| Provider and auth | `test_mini_provider.py`, `test_mini_auth.py`, `test_mini_siwc_auth.py`, `test_responses_siwc_contract.py` |
| Workspace tools | `test_mini_tools.py` |
| Compaction and windows | `test_mini_compaction.py`, `test_mini_window.py` |
| CLI and config | `test_mini_cli.py`, `test_mini_config.py` |
| Instructions, skills, memory, NOTE | `test_mini_instructions.py`, `test_mini_skills.py`, `test_mini_memory.py`, `test_mini_note.py` |
| Safety and modes | `test_mini_policy.py`, `test_mini_shell.py`, `test_mini_grants.py` |
| Extensions | `test_mini_hooks.py`, `test_mini_mcp.py`, `test_mini_subagents.py` |
| Recovery | `test_mini_snapshots.py`, `test_mini_goal.py` |
| Docs | `test_mini_docs.py` |
| Import firewall | `test_mini_boundary.py` |

## Real-provider checks

The acceptance runs use OpenAI Responses with an API key or ChatGPT plan usage
and are not part of the default test suite. They cover tool use, resume,
compaction, skills, build mode, and write/read. Record the command and observed
result in the change that adds a new acceptance.

## Docs checks

`test_mini_docs.py` and `python -m cade_mini.docs check` validate the manifest,
links, anchors, and generated catalogs. The docs job runs in CI.

## Standalone distribution

Build the wheel and source archive with `uv build --no-sources`. Install the
wheel in a clean virtual environment and check both `cade-mini --help` and
`python -m cade_mini --help` outside the source tree. No Cade package or command
should be present. See the [validation guidelines](../testing.md).
