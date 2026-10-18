# Development

Cade Mini is maintained independently of Cade. Work from this repository root.

## Setup

You need Python 3.12+ and uv:

```bash
uv sync --locked --extra dev --extra docs
mkdir -p e2e-results
```

Code lives in `src/cade_mini/`, tests in `tests/`, and the manual in
`docs/cade-mini/`. See [Architecture](architecture.md) for module boundaries.

## Checks

```bash
uv run --locked ruff check src/cade_mini/ tests/
uv run --locked ruff format --check src/cade_mini/ tests/
uv run --locked pyright src/cade_mini/
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
uv run --locked cade-mini --help
uv run --locked python -m cade_mini --help
uv run --locked cade-mini login --help
uv run --locked python -m cade_mini.docs check
uv build --no-sources
```

Follow the [validation guidelines](testing.md) to select additional checks.
Keep temporary workspaces and run evidence under `e2e-results/`. Never commit
credentials or raw provider traces. See the
[manual development page](cade-mini/development.md) for docs generation.

## Commits and releases

Review the diff and run checks before committing. Do not push or publish without
maintainer approval. Confirm that the Git remote points to the Mini repository,
not the separate Cade repository, before pushing.

The CI workflow checks code, tests, docs, startup, and distribution builds.
Version tags (`vX.Y.Z`) must match `pyproject.toml` and belong to `main`. The
release workflow publishes checked distribution artifacts after approval in the
GitHub `release` environment. Never move a published tag.
