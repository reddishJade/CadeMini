# Steering and queueing

The TUI keeps accepting input while a task runs. The UI thread renders events
and handles keys; the bridge runs the task in a worker thread with its own
`asyncio` event loop.

## Current design

```text
prompt_toolkit UI thread
  |  Enter / Alt+Enter / slash commands
  v
ApplicationSession
  |  steering, pending, cancel
  v
worker thread  --  asyncio.run(run_task)
  |
  v
structured event queue  -->  UI thread
```

- `ApplicationSession.run_task` calls the same `run` loop as one-shot mode.
- `service.steering` holds guidance for the next model step.
- `service.pending` holds follow-up tasks for the next turn.
- `service.cancel` wakes a pending provider stream immediately, even before its
  first output. Tools observe the same cancellation token at their safe points.
- The bridge converts loop events into structured events and queues them for the
  UI thread. The provider stream never blocks key handling.

## Commands during a run

| Input | Effect |
| --- | --- |
| `Enter` | Use the current busy-input mode: queue, steer, or replace |
| `Alt+Enter` | Append guidance at the next model step |
| `/steer TEXT` | Append guidance at the next model step |
| `/queue TEXT` | Queue TEXT as a follow-up task |
| `/queue followup` | Make `Enter` queue a follow-up task |
| `/queue steer` | Make `Enter` append guidance |
| `/queue interrupt` | Make `Enter` stop the task and run the message next |
| `/stop` or `Esc` | Interrupt work and restore undelivered queued input to the editor |
| `Ctrl+C` | Clear the editor without cancelling work; press again within 500 ms to exit |
| `/exit` or `/quit` | Exit the TUI |

A follow-up runs automatically when the current task reaches a terminal state.
Queue order is FIFO, and queued tasks stay in the same session. Explicit Esc or
`/stop` cancellation instead returns undelivered inputs to the editor, so they
do not immediately start another run.

`/steer` is not a prompt injection into a running provider request. The loop
checks the steering queue before each step, appends a `user` fact with
`source: runtime`, and builds the next request from the projection. This keeps
the session log as the source of truth.

## Session commands

`/new`, `/resume`, `/fork`, `/clone`, `/compact`, and
`/new-context` change session state. Run them when no task is active. During a
run, plain text is queued instead.

## Limits

- File workers finish their current operation before observing cancellation.
  Shell workers signal their child processes. Neither behavior rolls back file
  changes; use snapshots when restoration is needed.
- Steering applies between steps, not inside a provider stream or a running
  tool.
- The one-shot `--steer` flag seeds guidance before the first step. It is not a
  substitute for `/steer` during a run.

## Tests

`test_mid_run_steer_reaches_the_next_provider_request` blocks a tool until the
host delivers guidance, then checks that the next provider request contains the
steering fact.
