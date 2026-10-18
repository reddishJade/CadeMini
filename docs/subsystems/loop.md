# Loop

`run(log, text, ...)` is the only stage that writes during a turn.

## One run

1. Append the `user` fact.
2. For each step:
   - Project the branch.
   - If the full estimate exceeds budget, call the compactor.
   - Build the request.
   - If still over, call the window fallback.
   - Append a `request` audit fact.
   - Stream the provider.
   - Append the assistant fact.
   - Resolve and execute each tool call.
   - Append tool-result facts.
3. Append one `run` fact with termination, steps, usage, and error.

## Provider handling

The loop collects text deltas, tool calls, usage, and provider replay state. A
failure becomes an `attempt` fact. Retryable failures are retried once by
default; the retry delay is configurable. A terminal failure ends the run with
`provider_error` and never creates an assistant fact.

## Termination

- `completed`: the provider ended without tool calls.
- `step_limit`: the configured step count was reached.
- `context_error`: no request fits after compaction and window fallback.
- `aborted`: cancellation was requested.
- `provider_error`: the provider failed.

## Cancellation

A cancellation token is checked between steps and tool calls. Running tools get
the token; remaining calls receive interrupted results. The run records the
reason in its `run` fact.

## Events

The host receives text deltas, tool start, tool end, and final output. Events are
presentation; facts are durable. A host can drop events without losing history.

## Tests

`test_mini_loop.py` covers completion, tool use, approvals, retries, timeouts,
cancellation, context errors, and window fallback.
