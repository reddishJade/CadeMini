# Development

Cade Mini is maintained independently of Cade. Work from this repository root.

## Setup

You need Python 3.12+ and uv. Linux tool execution also requires usable
bubblewrap (`sudo apt-get install bubblewrap` on Debian/Ubuntu) and a host that
permits unprivileged user namespaces. Denied UID mappings are sandbox startup
errors, not approval failures. CI uses Ubuntu 22.04 without disabling host
security policies. macOS uses system `sandbox-exec`.
Native sandbox tests run on Linux and macOS; Windows
skips those tests and executes tools directly.

```bash
uv sync --locked --extra dev --extra docs
mkdir -p e2e-results
```

Code lives in `src/cade_mini/`, tests in `tests/`, and documentation in `docs/`.
See [Architecture](architecture.md) for module boundaries.

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

Follow the [validation guidelines](testing.md) to select focused checks.
Keep temporary workspaces and run evidence under `e2e-results/`. Never commit
credentials or raw provider traces.

## Documentation

The docs source is `docs/`. The manifest is `docs/docs.json`; it defines
navigation order and published pages. [docs/README.md](README.md) is the manual
entry point. [docs/AGENTS.md](AGENTS.md) defines documentation rules.

```bash
uv run --locked python -m cade_mini.docs catalogs --write
uv run --locked python -m cade_mini.docs llms --write
uv run --locked python -m cade_mini.docs build
uv run --locked python -m cade_mini.docs check
```

`check` validates the manifest, page files, relative links, anchors, and generated
catalogs. It uses only the standard library and runs in CI. `build` renders
`docs/index.html` and needs the `docs` extra.

`llms --write` updates `docs/llms.txt` and `docs/llms-full.txt`. `catalogs --write`
updates the config, event, and module references under `docs/design/`.
Do not edit generated files by hand.

To add a page:

1. Add the Markdown file under `docs/`.
2. Add it to a section in `docs/docs.json`.
3. Use relative links to other pages.
4. Regenerate the catalogs and site, then run the docs check.

Every Markdown file must be listed in the manifest unless it matches `exclude`.
The excluded files are agent instructions, decision records, and the postmortem
template. Put session format references under `docs/reference/` and historical
research under `docs/history/`.

## Boundaries

Mini owns the provider and process layers in `cade_mini.ai` and
`cade_mini.execution_env`. `tests/test_mini_boundary.py` rejects imports from
the old `cade` namespace. Do not add dependencies on the separate Cade project.

Public interfaces include the session schema, config fields, CLI flags, tool
names, and docs manifest. Change them with tests and documentation in the same
commit.

## Commits and releases

Review the diff and run checks before committing. Do not push or publish without
maintainer approval. Confirm that the Git remote points to the Mini repository,
not the separate Cade repository, before pushing.

The CI workflow checks code, tests, docs, startup, and distribution builds.
Version tags (`vX.Y.Z`) must match `pyproject.toml` and belong to `main`. The
release workflow publishes checked distribution artifacts after approval in the
GitHub `release` environment. Never move a published tag.
