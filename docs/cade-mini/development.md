# Development

## Setup

```bash
uv sync --locked --extra dev
mkdir -p e2e-results
```

Run checks from the repository root:

```bash
uv run --locked ruff check src/cade_mini/ tests/
uv run --locked ruff format --check src/cade_mini/ tests/
uv run --locked pyright src/cade_mini/
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
```

For Mini-focused work:

```bash
uv run --locked pytest -q \
  tests/test_mini_log.py \
  tests/test_mini_projection.py \
  tests/test_mini_request.py \
  tests/test_mini_loop.py \
  tests/test_mini_cli.py --basetemp=e2e-results/pytest
```

This suite covers only Mini and its owned lower layers; the separate Cade
project has its own tests.

## Documentation

The docs source is `docs/cade-mini/`. The manifest is
`docs/cade-mini/docs.json`; it defines navigation order and which pages are
published.

```bash
uv sync --locked --extra docs
uv run --locked python -m cade_mini.docs check
uv run --locked python -m cade_mini.docs build
uv run --locked python -m cade_mini.docs llms --write
uv run --locked python -m cade_mini.docs catalogs --write
```

`check` validates the manifest, page files, relative links, and anchors. The CI
workflow runs it without the docs extra, because checking uses only the standard
library.

`build` renders `docs/index.html` from Markdown and the manifest. It needs
`markdown-it-py`, installed by the `docs` extra.

`llms --write` updates `docs/cade-mini/llms.txt` and
`docs/cade-mini/llms-full.txt`. The first is an agent catalog; the second
contains the full page bodies.

### Adding a page

1. Add the Markdown file under `docs/cade-mini/`.
2. Add it to a section in `docs.json`.
3. Use relative links to other pages.
4. Run `python -m cade_mini.docs check`.
5. Regenerate `llms.txt` and the site.

Every Markdown file under `docs/cade-mini/` must be listed in the manifest,
except paths matching `exclude` (decision records are excluded).

### Writing style

- Use active voice and short sentences.
- Lead with the task, then the details.
- Use fenced code blocks with a language.
- Keep commands copy-pasteable.
- State limitations explicitly.

The repository language rules apply: code comments and docstrings are Chinese;
documentation, prompts, and UI text are English.

## Boundaries

Mini owns the provider and process layers in `cade_mini.ai` and
`cade_mini.execution_env`. `tests/test_mini_boundary.py` rejects imports from
the old `cade` namespace. Do not add dependencies on the separate Cade project.

Public interfaces include the session schema, config fields, CLI flags, tool
names, and docs manifest. Change them with tests and documentation in the same
commit.
