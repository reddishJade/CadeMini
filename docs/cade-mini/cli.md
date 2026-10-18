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
- `--json` emits JSONL events for a one-shot task.
- `--steer TEXT` injects guidance at the next model step; repeatable.
- `--queue TEXT` queues a task to run after the current task; repeatable.

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
- `/sessions` lists project sessions.
- `/fork [N|ENTRY]` forks before a recent user turn or entry. `N=1` is the latest.
- `/clone` copies the current branch to a new session.
- `/tree` shows entries; `*` marks the active branch.
- `/rewind [N]` creates a session without the latest N user turns.
- `/compact` summarizes older turns.
- `/new-context [NOTE]` starts a new window, optionally with a handoff note.
- `/mode [MODE]` shows or switches the execution mode.
- `/permissions [clear]` shows or clears session grants.
- `/undo [N]` restores the latest N snapshots.
- `/goal [TEXT|done|clear]` shows, sets, completes, or clears the goal.
- `/context` prints the session ID, provider, budget, and event count.
- `/steer TEXT` queues guidance for the next model step.
- `/queue TEXT` queues a task to run after the current one.
- `/stop` cancels the current task at the next safe point.
- `/help` prints the command list.
- `/exit` or `/quit` exits.

## REPL input

- `@path` attaches a workspace file to the task.
- `$skill` loads a skill body into the task.
- `!command` runs a shell command without contacting the model. Permission
  checks still apply.

```text
Review @src/cade/mini/core/loop.py and use $review-only
!git status
```

During a task, the REPL keeps reading input:

- `/steer TEXT` appends guidance at the next model step.
- `/queue TEXT` runs TEXT after the current task.
- `/stop` cancels at the next safe point.
- Other text is queued as a follow-up task.

Steering is applied between steps, not inside a provider stream or a running
tool. See [Steering and queueing](design/steering-queue.md).

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
