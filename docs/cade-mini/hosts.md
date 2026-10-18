# Hosts

Cade Mini has two hosts: the terminal CLI and a local web host. Both use the
same session log, request builder, tools, and policy.

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

## Web

```bash
cade-mini --web --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. The page submits tasks and displays the final
answer.

Endpoints:

- `GET /` serves the page.
- `POST /api/task` accepts `{"task": "..."}` and returns the answer,
  termination, steps, error, and session ID.
- `GET /api/sessions` returns the session list.

The web host serves one session for its lifetime. Requests are serialized so two
browser tabs cannot interleave turns.

Security:

- The server binds to loopback by default.
- There is no terminal to answer an approval prompt. Without `--yes` or a rule,
  writes and shell commands are denied while reads still work.
- `--read-only` denies mutations explicitly.

## Choosing a host

Use the terminal for interactive review and approvals. Use the web host for a
small local console or a demo. Use one-shot mode for scripts and CI, with
`--read-only` or explicit rules rather than a blanket `--yes`.

There is no separate TUI workbench. The REPL provides terminal interaction, and
the web host provides a browser surface.
