# Cade

Cade is a standalone Python coding agent with a durable, append-only
session log. It runs one-shot tasks or an inline terminal UI. It does not
require the earlier Cade project.

## Install and start

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), fd, and ripgrep.
Install fd and ripgrep on PATH; see the [development guide](docs/development.md#setup).
From this checkout:

```bash
uv sync --locked
uv run --locked cade login
uv run --locked cade -p "Read README.md and explain how this project runs."
```

Login uses Sign in with ChatGPT by default. For an OpenAI API key:

```bash
uv run --locked cade login --provider openai
uv run --locked cade --provider openai --model gpt-5.5 -p "Review this project."
```

For DeepSeek:

```bash
uv run --locked cade login --provider deepseek
uv run --locked cade --provider deepseek --model deepseek-flash -p "Review this project."
```

Cade supports only the Responses protocol. API keys are also read from
`OPENAI_API_KEY` and `DEEPSEEK_API_KEY`. Credentials live in
`~/.cade/auth.json`; it does not support the earlier Cade project's state
formats. See the
[quickstart](docs/quickstart.md) for browser login and setup.

To install the command outside the checkout:

```bash
uv tool install .
cade --help
```

## Use

```bash
cade
cade "Review recent changes"
cade -p "Review recent changes"
cade exec "Review recent changes"
cade --continue "Continue work"
cade --mode build "Fix the bug"
```

`cade` starts the inline terminal UI. A positional task is sent as its first
message, and the TUI stays open. Use `-p` or `exec` to run once and exit.
The TUI accepts commands, file and skill references, and shell shortcuts.

Build mode is the default: normal edits, tests, and builds run automatically.
Use `--mode ask` for approval before changes or `--mode plan` for planning.
Linux requires bubblewrap; macOS uses Seatbelt. Windows runs without an OS
sandbox. Network access is enabled by default. See
[Tools and security](docs/tools-security.md) for scopes, approvals, and limits.
Sessions and snapshots live in the project's `.cade/` directory.
Project settings live in `cade.json`.

Cade includes workspace tools, history retrieval, context compaction, skills,
memory, MCP tools, resources and prompts over stdio or HTTP, subagents,
file snapshots, goals, image reads, web search, and webpage retrieval. On Windows,
the `bash` tool requires Bash (for example, Git for Windows); `powershell` uses
PowerShell. The TUI supports
stepwise undo/redo, scoped rewind, and selected-file revert. Recovery covers
tracked file mutations, not arbitrary shell or external side effects.

- [User and developer guides](docs/README.md)
- [CLI](docs/cli.md) · [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md) · [Development](docs/development.md)
- [Generated docs site](docs/index.html)
- [中文文档](docs/zh/README.md) · [中文站点](docs/zh/index.html)

## Development

```bash
uv sync --locked --extra dev --extra docs
mkdir -p e2e-results
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
```

Production code lives in `src/cade/`; regression tests live in `tests/`.
The package owns its provider adapters and process execution code. It does not
depend on the earlier Cade project or provide a compatibility layer for it.

## License

MIT
