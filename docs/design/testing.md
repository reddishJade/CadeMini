# Testing

Tests are grouped by the layer they protect. Run the smallest group that covers
a change, then the full suite before a release.

## Commands

```bash
mkdir -p e2e-results
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
uv run --locked pytest -q tests/test_loop.py --basetemp=e2e-results/pytest
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked python -m cade.docs check
```

## Coverage map

| Area | Tests |
| --- | --- |
| Sessions | `test_log.py` |
| Projection and pairing | `test_projection.py` |
| Budget, pruning, wire format | `test_request.py` |
| Loop, approvals, retries, windows | `test_loop.py` |
| Provider and auth | `test_provider.py`, `test_auth.py`, `test_siwc_auth.py`, `test_responses_siwc_contract.py` |
| Workspace tools | `test_tools.py` |
| Compaction and windows | `test_compaction.py`, `test_window.py` |
| CLI and config | `test_cli.py`, `test_config.py` |
| Instructions, skills, memory, NOTE | `test_instructions.py`, `test_skills.py`, `test_memory.py`, `test_note.py` |
| Safety and modes | `test_policy.py`, `test_shell.py`, `test_grants.py` |
| Extensions | `test_hooks.py`, `test_mcp.py`, `test_subagents.py` |
| Recovery | `test_snapshots.py`, `test_goal.py` |
| Docs | `test_docs.py` |

## Real-provider checks

The acceptance runs use OpenAI Responses with an API key or ChatGPT plan usage
and are not part of the default test suite. They cover tool use, resume,
compaction, skills, build mode, and write/read. Record the command and observed
result in the change that adds a new acceptance.

## Docs checks

`test_docs.py` and `python -m cade.docs check` validate the manifest,
links, anchors, and generated catalogs. The docs job runs in CI.

## Standalone distribution

Build the wheel and source archive with `uv build --no-sources`. Install the
wheel in a clean virtual environment and check both `cade --help` and
`python -m cade --help` outside the source tree. The installed environment must
contain only this distribution's package and command. See the
[validation guidelines](../testing.md).
