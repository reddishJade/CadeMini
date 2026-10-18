# CLI

The `cade-mini` entry point runs one task, starts a REPL, manages sessions, or
serves the web host.

```bash
cade-mini [TASK] [options]
cade-mini exec TASK [options]
```

`exec` is an explicit alias for a one-shot task. Without a task, `cade-mini`
starts the interactive REPL.

## One-shot options

- `--project-root PATH` sets the workspace. The default is the current
  directory.
- `--config PATH` selects a config file. The default is
  `<project-root>/cade-mini.json`.
- `--provider NAME`, `--model NAME`, and `--effort EFFORT` override provider
  defaults.
- `--mode plan|build|act` selects the execution mode.
- `--read-only` / `--no-read-only` denies or permits writes and shell commands.
- `--yes` approves asks. Use it only in trusted, non-interactive contexts.
- `--auto-review` / `--no-auto-review` lets the model review ask-level calls.
- `--max-steps N` bounds model steps.
- `--context-window N` overrides the model context window.
- `--sandbox off|workspace|strict` selects the Linux bubblewrap policy.
- `--json` emits JSONL events for a one-shot task.

## Sessions

- `--list-sessions` prints ID, update time, and title, then exits.
- `--continue` resumes the latest session in the project.
- `--resume` resumes the latest session; `--resume ID` resumes a specific one.
- `--sessions-dir PATH` changes the session directory. A relative path is
  resolved against the project root.
- `--undo` restores the latest file snapshot and exits without contacting a
  provider.

`--continue` and `--resume` cannot be combined.

## Goals and web host

- `--goal TEXT` sets the workspace goal before running.
- `--web` starts the local web host instead of the REPL.
- `--host` and `--port` bind the web host. The default is `127.0.0.1:8765`.

## REPL commands

- `/new` starts a new session.
- `/resume [ID]` resumes the latest or a specific session.
- `/compact` summarizes older turns.
- `/new-context [NOTE]` starts a new window, optionally with a handoff note.
- `/mode [MODE]` shows or switches the execution mode.
- `/permissions [clear]` shows or clears session grants.
- `/undo` restores the latest snapshot.
- `/goal [TEXT|done|clear]` shows, sets, completes, or clears the goal.
- `/context` prints the session ID, provider, budget, and event count.
- `/help` prints the command list.
- `/exit` or `/quit` exits.

## Exit codes

- `0`: the run completed.
- `1`: the run ended without completion, or no snapshot was available for
  `--undo`.
- `2`: argument, configuration, or startup error.

## Examples

```bash
cade-mini --list-sessions
cade-mini --continue "Summarize the current state."
cade-mini --undo
cade-mini --mode plan "Find the smallest fix for the failing test."
cade-mini --mode build --yes "Fix the test and run pytest."
cade-mini --web --port 8765
```
