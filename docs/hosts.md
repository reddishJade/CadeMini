# Hosts

Cade has one terminal host with two modes: an interactive TUI and one-shot
execution. Both use the same session log, request builder, tools, policy, and
harness loop. Both modes use `app.bootstrap.create_session` and
`ApplicationSession.run_task`; the TUI also uses the service for commands and
session control.

## Terminal UI

Without `-p` or `exec`, the CLI starts the TUI in an interactive terminal:

```bash
cade
cade "Fix the failing test."
```

A positional task is sent once as the first TUI message. The TUI stays open
after the task completes.

The TUI keeps the conversation in a scrollable transcript, shows streaming
assistant text and tool progress, and handles approvals and questions without
leaving the application. Common keys:

- `Enter` submits a message; while running, it queues, steers, or interrupts according to the busy-input mode.
- `Alt+Enter` steers the running task at the next model step.
- `Alt+S` steers the running task; `Alt+Q` queues a follow-up task. These
  shortcuts submit plain editor text using a fixed action, regardless of the
  busy-input mode selected for `Enter`.
- `Shift+Tab` cycles `ask`, `build`, and `plan`.
- `Esc` interrupts the running task, including a model request that has not
  produced output yet. Undelivered queued input returns to the editor.
- When idle with empty input, double `Esc` opens the `/rewind` checkpoint selector.
  Use `/tree` to navigate branches in the same session, or `/fork` to copy history into a new session.
- `Ctrl+C` clears input without interrupting the task; press it again within
  500 ms to exit.
- `Ctrl+D` twice exits when idle with empty input.
- `Ctrl+O` toggles tool details; `Ctrl+T` toggles reasoning output.
- `Ctrl+Q` exits.

The TUI opens native selectors for models, modes, reasoning effort,
configuration, and permissions. Under `/config`, choose **TUI Rendering**,
then **Inline** or **Fullscreen**; the selected mode applies on the next start.
Inline uses terminal scrollback. Fullscreen uses the alternate screen, with
PageUp/PageDown for conversation history. Press `?` or run `/help` for the key
and command reference.

The TUI requires both stdin and stdout to be terminals. In a pipe or a
non-interactive process, use `-p TASK` or `exec TASK`.

The transcript shows context budget failures and output token limits as stopped
turns, including the provider error when available.

## One-shot

With `-p` or `exec`, the CLI runs once and exits:

```bash
cade -p "Fix the failing test."
cade exec "Fix the failing test."
```

One-shot mode supports `--json` for a JSONL event stream. Use `--read-only` or
explicit rules rather than a blanket `--yes` in automation and CI.
