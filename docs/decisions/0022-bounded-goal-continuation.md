# 0022: Continue explicitly armed goals within one bounded run

Status: accepted

## Context

Persisting an objective in request context does not prevent an unfinished goal
from stopping at a normal model reply. Codex and dsh schedule another round for
an active goal, separating durable state from live continuation authority.
Neither requires an independent evaluator for the first useful implementation.

## Decision

Keep Goal policy in the application. A generic harness continuation callback
adds a sourced input after a normal reply, preserving the same run lock,
recovery ownership, step allowance, cancellation, and usage accounting. Queued
human input wins. Errors, output limits, cancellation, and step exhaustion do
not automatically start a fresh allowance.

Share one `GoalStore` between application and tool. Explicit creation or resume
arms the current version in memory; loading or navigating history disarms it.
Persist paused and blocked states and a concrete blocker reason. A file lock
serializes changes, and completion/blocking compare the model's supplied
`updated_at` with the current version under that lock.

Tag automatic inputs as `user.source: goal`. They remain durable and make
sessions discoverable, without becoming human prompt choices or granting goal
replacement, clearing, pause, or resume authority to automatic rounds. Rollover
retains the root task and steering instead of repeated continuation prompts.

## Consequences

A normal final reply can no longer silently stop an armed unfinished objective.
The model still judges completion evidence and repeated blockers; the host
checks control, version, and finite execution, not semantic acceptance. There
is no persistent goal-wide token or time budget. Old binaries reject logs using
the new input source. Existing active/done goal files remain readable.

`tests/test_goal_runtime.py` covers early finals, shared budgets, cancellation,
errors, human priority, explicit resume, policy limits, rollover, persistence,
and tracked-write Undo. `tests/test_goal.py` covers state, activation, authority,
and stale completion. Window tests cover task anchors and steering.
