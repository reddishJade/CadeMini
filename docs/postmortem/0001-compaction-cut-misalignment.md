# 0001: Compaction cut pointed at a shadowed user entry

Date: 2026-10-18

Status: resolved

## Impact

After a prior compaction, the next compaction could choose a `first_kept_id`
from a user entry that was already shadowed. The summary covered the wrong tail,
and the retained window could omit the current task.

## Timeline

- The compactor collected all user entries from the branch.
- It collected user positions from the visible projection.
- It chose a cut by visible position but indexed the full branch entry list.
- A session with a prior compaction therefore pointed at an older, shadowed
  user entry.

## Root cause

Two lists with different lengths were treated as parallel arrays. A compaction
removes old user entries from the projection but leaves them in the branch. The
index into visible positions is not an index into all branch users.

## Fix

The compactor now aligns with the **last N** user entries, where N is the number
of visible user messages. A regression test builds a session with a prior
summary, a retained turn, and a new task, then asserts the new cut points at the
new task and the summary request contains the prior summary.

## Verification

`test_compaction.py::test_iterative_compaction_includes_prior_summary`
passes. The projected roles are `system, summary, user`.

## Follow-up

Any future cut logic must derive entry IDs from the same visible sequence it uses
to choose positions. See [Context and compaction](../subsystems/context-compaction.md).
