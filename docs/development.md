# Development

Cade is maintained independently of Cade. Work from this repository root.

## Setup

You need Python 3.12+ and uv. Linux tool execution also requires usable
bubblewrap (`sudo apt-get install bubblewrap` on Debian/Ubuntu) and a host that
permits unprivileged user namespaces. Denied UID mappings are sandbox startup
errors, not approval failures. CI uses Ubuntu 22.04 without disabling host
security policies. macOS uses system `sandbox-exec`.
File search requires fd and ripgrep on PATH. Install them before running tests:

```bash
# Debian or Ubuntu
sudo apt-get install ripgrep fd-find
# macOS
brew install ripgrep fd
# Windows (Scoop)
scoop install ripgrep fd
```

Cade also recognizes Debian/Ubuntu's `fdfind` executable.

Native sandbox tests run on Linux and macOS; Windows
skips those tests and executes tools directly.

```bash
uv sync --locked --extra dev --extra docs
mkdir -p tmp test-results
```

Code lives in `src/cade/`, tests in `tests/`, and documentation in `docs/`.
See [Architecture](architecture.md) for module boundaries.

### Restricted uv cache access

If uv cannot open its global cache `.lock` file because access is denied, use
`.uv-cache` inside the workspace. From the repository root in PowerShell:

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
```

This setting applies to the current terminal session. Run the usual uv commands
after setting it. For a single command, use
`uv --cache-dir ./.uv-cache run --locked ...` instead. Keep the cache
inside the workspace rather than changing global directory permissions.

## Dependencies

Use `uv add PACKAGE` to add a runtime dependency and `uv add --dev PACKAGE` for
development tools. Let uv generate version requirements and update `uv.lock`;
do not invent version bounds or edit dependency versions by hand. Add an extra
constraint only when a documented compatibility requirement justifies it.
Use `uv remove PACKAGE` to remove a dependency.

## Checks

```bash
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked pytest -q tests --basetemp=tmp/pytest
uv run --locked cade --help
uv run --locked python -m cade --help
uv run --locked cade login --help
uv run --locked python -m cade.docs check
uv build --no-sources
```

Follow the [validation guidelines](testing.md) to select focused checks.
Use `tmp/` for ordinary temporary workspaces, `test-results/` for regression
reports, and `e2e-results/` for end-to-end test results. Keep caches in dedicated
tool directories. Never commit credentials or raw provider traces.

## Documentation

The docs source is `docs/`. The manifest is `docs/docs.json`; it defines
navigation order and published pages. [docs/README.md](README.md) is the manual
entry point. [docs/AGENTS.md](AGENTS.md) defines documentation rules.

```bash
uv run --locked python -m cade.docs catalogs --write
uv run --locked python -m cade.docs llms --write
uv run --locked python -m cade.docs build
uv run --locked python -m cade.docs check
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

Cade owns five runtime packages: `ai`, `harness`, `coding`, `app`, and `hosts`.
The harness contains local process execution under `harness/execution/local`.
Lower layers never import higher layers; `tests/test_architecture.py` enforces
these boundaries. Do not add dependencies on the earlier Cade project.

Public interfaces include the session schema, config fields, CLI flags, tool
names, and docs manifest. Change them with tests and documentation in the same
commit.

## Commits and releases

Review the diff and run checks before committing. Do not push or publish without
maintainer approval. Confirm that the Git remote points to this repository, not
the earlier Cade project, before pushing.

The CI workflow checks code, tests, docs, startup, and distribution builds.
Version tags (`vX.Y.Z`) must match `pyproject.toml` and belong to `main`. The
release workflow publishes checked distribution artifacts after approval in the
GitHub `release` environment. Never move a published tag.
