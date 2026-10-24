# 0016: Preserve response observations for session restoration

Status: accepted

## Context

The TUI shows reasoning summaries, response performance, and stop notices while
a task runs. Reopening a session previously lost these observations and could
make an incomplete answer appear complete.

## Decision

The harness stores the raw reasoning summary and measured TTFT/TPS as optional
`reasoning` and `response_metrics` fields on the corresponding assistant fact.
These fields belong to the harness and live for the lifetime of that record.
They do not enter conversation projection or provider replay data. Attempts
discarded for a retry remain excluded from assistant history.

Hosts derive stop notices from existing run outcomes. Hosts format observations
when restoring the active branch; formatted output is not durable state.

## Consequences

Session restoration retains reasoning summaries and request performance without
adding another event stream or a mutable sidecar. Logs grow by the size of the
provider's reasoning summary. Missing measurements remain unknown.

[Session schema](../reference/session-schema.md) defines the fields.
`tests/test_tui_boundaries.py` checks restoration, retries, and branch selection.
`tests/test_tui_controls.py` checks cancellation during reasoning.
`tests/test_log.py` checks rejection of invalid observation payloads.
