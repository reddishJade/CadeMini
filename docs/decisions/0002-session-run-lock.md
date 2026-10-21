# 0002: Reject concurrent session runs

Status: accepted

## Context

An append lock preserves the parent chain but cannot protect a whole model/tool
turn. Concurrent loops can interleave messages or recover a tool still running.
Waiting for another turn would also hide an unintended concurrent invocation.

## Decision

`run()` holds `session-<id>.run.lock` from recovery through termination. A competing
run raises `LogError` immediately, before appending facts. Each acquisition uses
a separate lock object, including calls in the same thread. CLI manual compaction
and window changes use the same lock. OS locks release when the process exits.

The append lock remains separate. Storage clients that perform multi-event
operations must acquire the run lock themselves; `append()` alone does not
serialize whole turns.

## Consequences

Hosts can report a busy session without blocking the event loop. Cancellation and
exceptions release the lock. `test_loop.py` covers concurrent async runs and
resume after cancellation; `test_log.py` covers competing processes.
