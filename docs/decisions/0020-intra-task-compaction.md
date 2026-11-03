# 0020: Compact within a user task

Status: accepted

## Context

User-only cuts cannot reduce a single long-running task. Pi retains a recent
suffix beginning at a user or assistant message; Codex can replace execution
history with retained user input and a summary. Cade already records compaction
boundaries separately from the original facts.

## Decision

Prefer existing user boundaries. When none fits, allow `first_kept_id` to point
to an assistant event. Keep its tool calls and all results together. Summarize
the removed prefix and any previous summary, including the original objective,
constraints, verification, and unfinished work. Do not add synthetic user tasks.
Request admission remains lossless; an oversized latest tool group can still
terminate with `context_error`. Window boundaries are unchanged.

## Consequences

One user task can compact repeatedly without losing raw evidence. Existing logs
remain readable. Older binaries reject new logs whose compaction boundary is an
assistant event; use the updated binary to resume those logs.

`tests/test_compaction.py` covers repeated in-task cuts, restart, history lookup,
multiple tool results, and oversized latest groups. `tests/test_projection.py`
checks valid boundaries and rejects orphan results.
