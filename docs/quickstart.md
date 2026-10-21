# Quickstart

Cade requires Python 3.12 or newer. The repository uses `uv` for
development and for the `cade` entry point.

## Install and authenticate

From the repository root:

```bash
uv sync --locked
```

Cade owns its credential store at `~/.cade/auth.json`. Log in with
Sign in with ChatGPT (SIWC) in a browser:

```bash
uv run --locked cade login
```

The browser flow registers Cade on first use and reuses that registration
on later sign-ins. It stores a stable host ID at `~/.cade/host.json`. SIWC
has no device-code flow. On a headless host, complete the login locally, copy
`auth.json`, and let the remote host create its own host ID on the next login.
Or use an API key.

For an OpenAI API key, use a hidden key prompt or set the environment variable:

```bash
uv run --locked cade login --provider openai
export OPENAI_API_KEY=...
uv run --locked cade --provider openai --model gpt-5.5 "Review this project."
```

DeepSeek uses the same Responses protocol with its own API key:

```bash
uv run --locked cade login --provider deepseek
export DEEPSEEK_API_KEY=...
uv run --locked cade --provider deepseek --model deepseek-flash "Review this project."
```

The default provider is `chatgpt`. Set `provider` and `model` in
`cade.json` to choose other startup defaults. Cade supports only the
Responses protocol: `chatgpt` for ChatGPT plan usage, `openai` for an OpenAI
API key, and `deepseek` for a DeepSeek API key. State from the earlier Cade
project is not compatible; use clean `~/.cade/` and workspace `.cade/`
directories.

## Run one task

```bash
uv run --locked cade "Read README.md and explain the entry point in three bullets."
```

The same command can be written explicitly:

```bash
uv run --locked cade exec "Read README.md and explain the entry point."
```

Cade runs in the current directory by default. Use `--project-root` to
point at another workspace.

## Choose a mode

- `act` is the default. Reads run directly; writes and shell commands ask.
- `build` allows structured writes and read-only shell commands. Mutating shell
  commands still ask.
- `plan` hides write and edit tools and denies mutations. Read-only shell
  commands are allowed.

```bash
uv run --locked cade --mode plan "Propose a fix for the failing test."
uv run --locked cade --mode build "Fix the bug and run the test."
```

For non-interactive automation, `--yes` approves asks, and `--read-only` denies
writes and shell commands. Prefer rule-based allows over `--yes` for shared
repositories; see [Configuration](configuration.md).

## Continue work

Sessions live under `.cade/sessions` by default.

```bash
uv run --locked cade --list-sessions
uv run --locked cade --continue "What did you change?"
uv run --locked cade --resume SESSION_ID "Continue the task."
```

Omitting the task starts the full-screen TUI. See [CLI](cli.md) for interactive
commands and [Hosts](hosts.md) for key bindings.

## Build the docs

```bash
uv sync --locked --extra docs
uv run --locked python -m cade.docs check
uv run --locked python -m cade.docs build
```

Open `docs/index.html`. The docs source is under `docs/`.
