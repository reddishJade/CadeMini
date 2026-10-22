# 0014: Avoid rescanning and rewriting unchanged storage

Status: accepted

## Context

Normalized request audits make session logs grow beyond model-visible history.
Reloading and validating all records before each append makes small writes depend
on total log size. Snapshot capture also read and rewrote the entire index.
Local synthetic measurements confirmed both costs grow with history.

## Decision

Keep the current session and snapshot record shapes. Cache validated session
entries and their ID index inside each `SessionLog`. Under the existing file
lock, check file identity, size, and nanosecond modification/change timestamps.
Reload and fully validate when those values change. Validate each new entry
against the verified ID index and update the cache only after write and sync.
Return independent copies so callers cannot change cached facts.

Snapshot capture appends and syncs one newline-terminated index record. Ignore
an incomplete last line and truncate it before the next append. A complete
invalid record remains an error. Keep per-instance validated index state and
invalidate it on file changes. The standalone snapshot undo primitive retains
atomic replacement of its shortened index. Sync the root directory after each
capture on POSIX so the new snapshot directory and index remain durable.

Preserve append and run locks, tail repair after validation, full request audits,
and audit-before-provider ordering. Add no persistent cache or secondary store.

## Consequences

Warm appends no longer parse historical requests or rewrite snapshot history.
Opening a log and refreshing after another writer still require full validation.
Caching retains parsed history in memory, and public reads still copy payloads.
Normal capture commits at the terminating newline rather than index replacement.

`test_log.py` covers independent returned data, replacement and truncation,
external corruption, failed sync, and cooperating processes. `test_snapshots.py`
covers cooperating stores, interrupted index tails, complete corruption, and
undo after index replacement. Recovery and history tests retain their existing
contracts.
