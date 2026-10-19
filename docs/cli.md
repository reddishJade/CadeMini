# CLI

The `cade-mini` entry point runs one task, starts an interactive TUI, or manages sessions.

```bash
cade-mini [TASK] [options]
cade-mini exec TASK [options]
```

`exec` is an explicit alias for a one-shot task. Without a task, `cade-mini`
starts the inline TUI on an interactive terminal. Use `--no-tui` for the
legacy REPL.

## Interactive terminal

The TUI uses main-screen terminal scrollback, streamed Markdown, tool/reasoning
folding and project/session/model selectors. A new project can be configured
using the provider/model setup wizard; ChatGPT uses SIWC browser login and
OpenAI uses an API key. Existing login credentials remain in the Mini store.

- Enter sends a prompt; while busy, Enter queues, Alt+Enter steers, Ctrl+C cancels.
- Ctrl+J inserts a newline; Ctrl+O expands tool details (and approval previews);
  Ctrl+T expands model reasoning.
- PageUp/PageDown scroll a long approval preview; Enter accepts the selected
  decision; Esc denies it.
- / commands, @file references, $skills, !shell, and model selection have
  in-terminal completion.
- Shift+Tab changes mode; Ctrl+D twice exits on an empty prompt. Ctrl+Q exits.
- /btw QUESTION asks an isolated streaming side question without logging it.
- Ctrl+K opens the searchable command palette, Ctrl+P the model selector, and
  Ctrl+G the session selector; ? opens the palette at an empty prompt.
- Session, model, configuration and tool selectors support fuzzy search;
  use ↑/↓ and Enter, Tab to focus the list, Esc to close.
- The model selector includes a custom model ID entry.
- Consecutive completed read/search tool calls are grouped in compact view;
  Ctrl+O expands their individual calls, results and edit diffs.
- When the model invokes `question`, the TUI collects single choices, multiple
  choices or a free-text answer without restarting the agent. Ctrl+J inserts a
  newline in a free-text answer; Enter submits it.
- An existing workspace with missing or expired credentials still opens the TUI
  in an explicit sign-in-required state; use `/login` to reconnect. One-shot
  tasks keep their fail-fast authentication behavior.
- `/tool NAME JSON` runs one tool interactively with the same hooks, approvals,
  and folded output as model-initiated tools.

A task or `exec TASK` continues to run once and exit, without opening the TUI.

## Login

```bash
cade-mini login
cade-mini login --provider openai
```

`--provider` defaults to `chatgpt`, which uses Sign in with ChatGPT (SIWC)
in a browser. `openai` uses `api_key` with a hidden input prompt. Credentials
are stored in `~/.cade-mini/auth.json`; the stable SIWC host ID is stored in
`~/.cade-mini/host.json`. Login does not change project provider or model
settings. Cade Mini supports only the OpenAI Responses protocol.

## One-shot options

- `--project-root PATH` sets the workspace. The default is the current
  directory.
- `--config PATH` selects a config file. The default is
  `<project-root>/cade-mini.json`. In the TUI, `/config` edits this same file
  rather than silently writing the default.
- `--provider NAME`, `--model NAME`, and `--effort EFFORT` override provider
  defaults.
- `--mode plan|build|act` selects the execution mode.
- `--read-only` / `--no-read-only` denies or permits writes and shell commands.
- `--yes` approves asks. Use it only in trusted, non-interactive contexts.
- `--auto-review` / `--no-auto-review` lets the model review ask-level calls.
- `--max-steps N` bounds model steps.
- `--context-window N` overrides the model context window.
- `--json` emits JSONL events for a one-shot task.
- `--tui` / `--no-tui` forces or disables the inline interactive terminal.
- `--steer TEXT` injects guidance at the next model step; repeatable.
- `--queue TEXT` queues a REPL task to run after the current task; repeatable.

## Sessions

- `--list-sessions` prints ID, update time, and title, then exits.
- `--continue` resumes the latest session in the project.
- `--resume` resumes the latest session; `--resume ID` resumes a specific one.
- `--sessions-dir PATH` changes the session directory. A relative path is
  resolved against the project root.
- `--undo` restores the latest file snapshot and exits without contacting a
  provider.

`--continue` and `--resume` cannot be combined.

## Goals

- `--goal TEXT` sets the workspace goal before running.

## Interactive commands (TUI and legacy REPL)

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

## Interactive input

- `@path` attaches a workspace file to the task.
- `$skill` loads a skill body into the task.
- `!command` runs a shell command without contacting the model. Permission
  checks still apply.

```text
Review @src/cade_mini/core/loop.py and use $review-only
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
- `130`: login was cancelled.

## Examples

```bash
cade-mini --list-sessions
cade-mini --continue "Summarize the current state."
cade-mini --undo
cade-mini --mode plan "Find the smallest fix for the failing test."
cade-mini --mode build --yes "Fix the test and run pytest."
```
