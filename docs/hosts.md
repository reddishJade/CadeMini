# Hosts

Cade Mini runs one shared session log, request builder, tools and policy.
The terminal host has three modes: inline TUI, line-oriented REPL and one-shot.

## Terminal

Running `cade-mini` on an interactive terminal opens the main-screen inline
TUI with native scrollback, streamed Markdown, a multiline editor, completion,
searchable session/model/configuration pickers, grouped exploration tools,
streamed Markdown, reasoning folding, and interactive approvals:

```bash
cade-mini
```

For scripts or terminals without rich interaction, use the line-based REPL:

```bash
cade-mini --no-tui
```

Pass a task to run once, independently of either interactive mode:

```bash
cade-mini "Fix the failing test."
cade-mini exec "Fix the failing test."
```

The TUI also provides an interactive `question` tool for single-choice,
multiple-choice and multiline free-text answers during agent execution.
Ctrl+K searches commands, Ctrl+P selects a model and Ctrl+G selects a session.
When an existing workspace needs fresh credentials, the terminal remains
available and prompts for `/login` before running a model task. Manual `!shell`
and `/tool` calls use the same authorization and hook lifecycle as agent tools.

One-shot mode supports `--json` for a JSONL event stream. The interactive
hosts do not. First-time TUI setup selects a model and configures SIWC
ChatGPT browser authentication or an OpenAI API key when needed.

## Choosing a mode

The inline TUI is the default for everyday terminal work. Choose one-shot for
scripts and CI, with `--read-only` or explicit rules rather than a blanket
`--yes`. `--no-tui` preserves line-based terminal interaction.
