# Terminal workspace

The prompt-toolkit host uses an inline transcript (`full_screen=False`). It does
not capture mouse scrolling or use the alternate screen. Pickers, input forms,
permissions and streaming output share one application event loop.

## Responsibilities

- `app.py`: layout, keyboard actions, background runs and recovery.
- `bridge.py`: project Mini session and loop events into terminal views.
- `state.py`, `rendering.py`, `inline_renderer.py`: transcript state and rendering.
- `completion.py`, `widgets.py`: input completion and highlighting.
- `command_handlers.py`: dispatch through the shared interaction service.
- `settings.py`, `login_flow.py`, `config_registry.py`: model, login and settings UI.
- `approval.py`: permission previews; supported grants are once or this session.

Session, provider and command operations belong to
[`InteractionService`](../interaction.py), not the renderer. The command catalog
is shared by help and completion. Native selectors adapt those operations to
terminal controls without opening nested terminal event loops.

## Input and lifecycle

- Enter submits; busy Enter queues by default; Alt+Enter steers; Ctrl+J inserts a newline.
- Esc cancels active work and restores undelivered queued input to the editor.
- Ctrl+C clears input; press it again within 500 ms to exit.
- Idle double Esc with empty input opens the full tree selector. Navigation
  stays in the same session; fork and clone create independent sessions.
  These operations do not restore workspace files.
- New interactive sessions stay in memory until the first conversation message.
- API-key forms mask input and do not append secrets to input history.
- Exit cancels active work; saved conversations show a standalone Mini resume command.

Long transcripts enter terminal scrollback. Folding or resizing may require
clearing and replaying that scrollback, including content from before Mini started.
Busy-input queues are process-local, not a durable inbox.

See the [CLI reference](../../../../docs/cli.md) for supported commands.
