# Hosts

Cade Mini has one host: the terminal CLI. It uses the session log, request
builder, tools, and policy.

## Terminal

Without a task, the CLI starts a REPL:

```bash
cade-mini
```

The REPL prints tool progress to stderr, streams assistant text to stdout, and
accepts the commands listed in [CLI](cli.md). With a task, it runs once and
exits:

```bash
cade-mini "Fix the failing test."
cade-mini exec "Fix the failing test."
```

One-shot mode supports `--json` for a JSONL event stream. The REPL does not.

## Choosing a mode

Use the REPL for interactive review and approvals. Use one-shot mode for scripts
and CI, with `--read-only` or explicit rules rather than a blanket `--yes`.

There is no separate TUI workbench. The REPL provides terminal interaction.
