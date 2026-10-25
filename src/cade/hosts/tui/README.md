# Terminal workspace

The prompt-toolkit host supports inline and fullscreen rendering. Inline is the
default and retains terminal scrollback and native selection; fullscreen uses
the alternate screen and PageUp/PageDown for the conversation viewport.
Select **TUI Rendering** under `/config` to change the mode on the next start.
Pickers, input forms, permissions, and streaming output share one application
event loop in either mode.

## Responsibilities

- `app.py`: layout, keyboard actions, background runs and recovery.
- `bridge.py`: project Cade session and loop events into terminal views.
- `state.py`, `rendering.py`, `inline_renderer.py`: transcript state and rendering.
- `completion.py`, `widgets.py`: input completion and highlighting.
- `command_handlers.py`: dispatch through the shared interaction service.
- `settings.py`, `login_flow.py`, `config_registry.py`: model, login and settings UI.
- `approval.py`: permission previews; supported grants are once or this session.

Session, provider and command operations belong to
[`ApplicationSession`](../../app/session.py), not the renderer. The command catalog
is shared by help and completion. Native selectors adapt those operations to
terminal controls without opening nested terminal event loops.

## Input and lifecycle

- Enter submits; busy Enter queues by default; Alt+Enter steers; Ctrl+J inserts a newline.
- Esc cancels active work and restores undelivered queued input to the editor.
- Ctrl+C clears input; press it again within 500 ms to exit.
- Idle double Esc with empty input opens rewind, then recovery scope and
  confirmation. `/tree` remains conversation-only branch navigation.
- Undo/redo applies one saved action; rewind/revert previews tracked files and
  refuses conflicts. No recovery operation reruns tools or model requests.
- New interactive sessions stay in memory until the first conversation message.
- API-key forms mask input and do not append secrets to input history.
- Exit cancels active work; saved conversations show a standalone Cade resume command.

Long transcripts enter terminal scrollback. Folding, changing `/verbose`, or
resizing may require clearing and replaying the scrollback, including content
from before Cade started. The TUI defaults to `auto`: active turns show every
tool call individually with collapsed thinking and tool output. Once the turn
finishes, the activity becomes a single expandable line while assistant
messages remain visible. `Ctrl+O` expands completed activity; `/verbose high`
shows full details. `low` hides activity, `medium` restores compact output,
and `high` expands details. All event data stays in the session log;
permission requests and notices remain visible.
Busy-input queues are process-local, not a durable inbox.

See the [CLI reference](../../../../docs/cli.md) for supported commands.
