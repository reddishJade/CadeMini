# 0010: TUI is the only interactive host

Status: accepted

## Context

Cade Mini previously shipped a line-oriented REPL alongside one-shot execution.
The terminal UI now uses `InteractionService` for sessions, approvals, goals,
queueing, and steering. Cade no longer keeps a REPL, and maintaining two
interactive hosts would duplicate host behavior and tests.

## Decision

Without a task, `cade-mini` starts the TUI. The TUI requires stdin and stdout to
be terminals; otherwise the CLI exits with status 2 and asks for a task.
One-shot execution remains available as `cade-mini TASK` or
`cade-mini exec TASK`.

The `--repl`, `--tui`, and `--queue` options are removed. REPL-only input,
approval, and live-task helpers are removed with their tests. `--steer` applies
only to a one-shot task.

## Consequences

There is one interactive implementation to maintain and test. Scripts and
pipes that need a task must pass it explicitly. The TUI host uses the same
session log, policy, tools, and loop as one-shot mode.

`test_cli_interactive_requires_terminal` enforces the non-terminal behavior.
`test_mid_run_steer_reaches_the_next_provider_request` covers steering through
the shared loop.
