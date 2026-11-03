# 0021: Reset context within the current task

Status: accepted

## Context

A window anchored at the latest user entry retains all execution from that
entry. It cannot reduce a single task that exceeds the input budget. Codex's
experimental token-budget mode installs fresh context without generating a
compaction summary, with budget reminders for saving working state.

## Decision

Extend `window` with optional `clear_history`, default false. When true,
`start_id` remains the task anchor, but execution history resumes after the
window event. The handoff contains the original human task, subsequent steering,
and note or recovery guidance. Keep all original log entries. Refuse to reset
an unfinished tool batch. Existing user-boundary windows keep their semantics.

Manual rollover and automatic overflow fallback use this reset. When an
application request reaches 80% of its input budget and a reminder fits, include
one audited NOTE reminder without an extra model request. A sudden large result
may bypass the reminder. No note write or note freshness is guaranteed.

## Consequences

One task can cross multiple windows without resubmission. Fixed context and
instructions must still fit. This is separate from summarizing compaction and
adds no second agent loop. Old binaries reject the new optional payload field.

`tests/test_window.py`, `tests/test_loop.py`, and `tests/test_application.py`
cover restart, branch copying, steering, incomplete tools, repeated rollover,
reminder auditing, and manual and automatic application paths.
