# Cade Mini

Cade Mini is a standalone Python coding agent with a durable, append-only
session log. It runs one-shot tasks or an interactive terminal REPL. It does
not require Cade.

## Install and start

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). From this checkout:

```bash
uv sync --locked
uv run --locked cade-mini login
uv run --locked cade-mini "Read README.md and explain how this project runs."
```

Login uses Sign in with ChatGPT by default. For an OpenAI API key:

```bash
uv run --locked cade-mini login --provider openai
uv run --locked cade-mini --provider openai --model gpt-5.5 "Review this project."
```

Cade Mini supports only the OpenAI Responses protocol. Provider API keys are
also read from `OPENAI_API_KEY`. Credentials live in `~/.cade-mini/auth.json`;
Cade credentials and settings are not read. See the
[quickstart](docs/quickstart.md) for browser login and setup.

To install the command outside the checkout:

```bash
uv tool install .
cade-mini --help
```

## Use

```bash
cade-mini
cade-mini exec "Review recent changes"
cade-mini --continue "Continue work"
cade-mini --mode build "Fix the bug"
```

Build mode is the default: normal edits, tests, and builds run automatically.
Use `--mode ask` for approval before changes or `--mode plan` for planning.
Linux requires bubblewrap; macOS uses Seatbelt. Windows runs without an OS
sandbox. Network access is enabled by default. See
[Tools and security](docs/tools-security.md) for scopes, approvals, and limits.
Sessions and snapshots live in the project's `.cade-mini/` directory.
Project settings live in `cade-mini.json`.

Mini includes workspace tools, history retrieval, context compaction, skills,
memory, hooks, MCP tools, subagents, file snapshots, and goals.

- [User and developer guides](docs/README.md)
- [CLI](docs/cli.md) · [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md) · [Development](docs/development.md)
- [Generated docs site](docs/index.html)

## Development

```bash
uv sync --locked --extra dev --extra docs
mkdir -p e2e-results
uv run --locked ruff check src/cade_mini/ tests/
uv run --locked ruff format --check src/cade_mini/ tests/
uv run --locked pyright src/cade_mini/
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
```

Production code lives in `src/cade_mini/`; regression tests live in `tests/`.
The package owns its provider adapters and process execution code. There is no
`cade` command, `cade` Python package, or compatibility layer.

## License

MIT
