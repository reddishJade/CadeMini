# Steering and queueing

Cade Mini's REPL can read input while a task is running. The host does not block
the terminal on `_run_task`.

## Why the old REPL blocked

The first REPL called `input()` in the main thread and then ran the async loop
with `asyncio.run`. While that call was active, the terminal was not read, so
`/steer` and `/queue` could only affect the next task.

## Current design

```text
stdin
  |
  v
_InputPump thread  -- _Line -->  event queue
                                  |
task thread  -- _ApprovalRequest / _TaskDone --> event queue
                                  |
                                  v
                         REPL main thread
```

- `_InputPump` reads one line at a time and never calls the model.
- `_run_task` runs in a worker thread with its own `asyncio` event loop.
- The REPL main thread owns the event queue and the session state.
- `_HostApprover` sends `_ApprovalRequest` to the main thread and waits. The
  main thread reads the answer from the same input pump, so approvals do not
  compete with a second `input()`.
- `!command` runs in the main thread and uses `_ConsoleApprover`, which reads
  the answer directly from the event queue.

## Commands during a run

| Input | Effect |
| --- | --- |
| `/steer TEXT` | Append guidance at the next model step |
| `/queue TEXT` | Run TEXT as a new task after the current task |
| `/stop` or `/cancel` | Cancel the current task at the next safe point |
| other text | Same as `/queue`: run it after the current task |
| `/exit` | Cancel, wait for the task to settle, then exit |

`/steer` is not a prompt injection into a running provider request. The loop
checks the steering queue before each step, appends a `user` fact with
`source: runtime`, and builds the next request from the projection. This keeps
the session log as the source of truth.

## Queue semantics

- A queued task starts automatically when the current task reaches a terminal
  state.
- Queue order is FIFO.
- A queued task runs in the same session; it is not a fork.
- `/fork`, `/clone`, `/rewind`, and session commands are main-thread commands.
  They are only handled when no task is active. During a run they are treated as
  queued text; finish or stop the run first.

## Limits

- Tools already executing are not killed. `/stop` takes effect at the next
  cancellation check.
- The input pump uses plain `input()`, not a full line editor. Editing,
  history, and completion are terminal features.
- Approval answers are read as the next line. Do not type a task while an
  approval prompt is waiting; it is consumed as the answer.
- The one-shot `--steer` flag seeds guidance before the first step. It is not a
  substitute for `/steer` during a run.

## Tests

- `test_live_input_handles_steer_and_queue`
- `test_live_input_is_read_while_worker_is_running`
- `test_mid_run_steer_reaches_the_next_provider_request`
- `test_repl_reads_steer_while_a_bash_tool_is_running`
- `test_host_approver_routes_through_events`
- `test_live_input_can_stop_the_task`

The end-to-end test blocks a tool until the host delivers `/steer`, then checks
that the next provider request contains the steering fact.
