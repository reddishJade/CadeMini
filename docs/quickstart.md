# Quickstart

Cade Mini requires Python 3.12 or newer. The repository uses `uv` for
development and for the `cade-mini` entry point.

## Install and authenticate

From the repository root:

```bash
uv sync --locked
```

Cade Mini owns its credential store at `~/.cade-mini/auth.json`. Log in with
Sign in with ChatGPT (SIWC) in a browser:

```bash
uv run --locked cade-mini login
```

The browser flow registers Cade Mini on first use and reuses that registration
on later sign-ins. It stores a stable host ID at `~/.cade-mini/host.json`. SIWC
has no device-code flow. On a headless host, complete the login locally, copy
`auth.json`, and let the remote host create its own host ID on the next login.
Or use an API key.

For an OpenAI API key, use a hidden key prompt or set the environment variable:

```bash
uv run --locked cade-mini login --provider openai
export OPENAI_API_KEY=...
uv run --locked cade-mini --provider openai --model gpt-5.5 "Review this project."
```

DeepSeek uses the same Responses protocol with its own API key:

```bash
uv run --locked cade-mini login --provider deepseek
export DEEPSEEK_API_KEY=...
uv run --locked cade-mini --provider deepseek --model deepseek-flash "Review this project."
```

The default provider is `chatgpt`. Set `provider` and `model` in
`cade-mini.json` to choose other startup defaults. Cade Mini supports only the
Responses protocol: `chatgpt` for ChatGPT plan usage, `openai` for an OpenAI
API key, and `deepseek` for a DeepSeek API key. Mini does not read Cade's
credentials, skills, or settings.

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

## Build the docs

```bash
uv sync --locked --extra docs
uv run --locked python -m cade_mini.docs check
uv run --locked python -m cade_mini.docs build
```

Open `docs/index.html`. The docs source is under `docs/`.
