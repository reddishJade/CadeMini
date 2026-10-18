# Quickstart

Cade Mini requires Python 3.12 or newer. The repository uses `uv` for
development and for the `cade-mini` entry point.

## Install and authenticate

From the repository root:

```bash
uv sync --locked
```

Cade Mini reuses Cade's credential store. Configure a provider once with the
Cade login command, or set the provider's API key environment variable:

```bash
cade login
# or
export DEEPSEEK_API_KEY=...
```

Codex uses the stored Codex OAuth credential. `--provider codex` selects it; no
separate Mini login command exists.

## Run one task

```bash
uv run --locked cade-mini "Read README.md and explain the entry point in three bullets."
```

The same command can be written explicitly:

```bash
uv run --locked cade-mini exec "Read README.md and explain the entry point."
```

Cade Mini runs in the current directory by default. Use `--project-root` to
point at another workspace.

## Choose a mode

- `act` is the default. Reads run directly; writes and shell commands ask.
- `build` allows structured writes and read-only shell commands. Mutating shell
  commands still ask.
- `plan` hides write and edit tools and denies mutations. Read-only shell
  commands are allowed.

```bash
uv run --locked cade-mini --mode plan "Propose a fix for the failing test."
uv run --locked cade-mini --mode build "Fix the bug and run the test."
```

For non-interactive automation, `--yes` approves asks, and `--read-only` denies
writes and shell commands. Prefer rule-based allows over `--yes` for shared
repositories; see [Configuration](configuration.md).

## Continue work

Sessions live under `.cade-mini/sessions` by default.

```bash
uv run --locked cade-mini --list-sessions
uv run --locked cade-mini --continue "What did you change?"
uv run --locked cade-mini --resume SESSION_ID "Continue the task."
```

Omitting the task starts the interactive REPL. See [CLI](cli.md) for all REPL
commands.

## Open the web host

```bash
uv run --locked cade-mini --web --port 8765
```

Open `http://127.0.0.1:8765`. The web host has no terminal prompt, so writes and
shell commands are denied unless you start it with `--yes` or use rules. The
host serves one durable session for its lifetime.

## Build the docs

```bash
uv sync --locked --extra docs
uv run --locked python -m cade.mini.docs check
uv run --locked python -m cade.mini.docs build
```

Open `docs/mini/index.html`. The docs source is under `docs/cade-mini/`.
