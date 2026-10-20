# 0003: Verify file state before undo

Status: accepted

## Context

A pre-change payload alone cannot distinguish an agent change from a later user
edit. Restoring it unconditionally can overwrite user work. Capturing the entire
workspace would add cost and change the existing coverage boundary.

## Decision

Keep per-file coverage. Each snapshot stores an immutable before manifest,
a content-addressed original blob, and an immutable completion record with the
actual post-change content hash, size, and mode. Absence is an explicit null state.
`change(path)` holds a project snapshot lock across capture, mutation, and completion.
Manual callers use `capture()` followed by `complete()`.

Undo verifies the current state against the completion record and validates the
original blob before changing the target. Reject conflicts, symlink targets,
unknown outcomes, and legacy records without post-change state. Do not consume
the index on these failures. Replace restored files and the index atomically.

## Consequences

Old snapshot payloads remain on disk for manual inspection; no automatic migration
can infer their post-change state. New snapshots can be recorded above old ones.
Undo remains per-file, not a transaction across an entire shell command. Unknown
shell effects remain outside coverage. The lock coordinates Cade snapshot clients,
not editors or other external writers; stop those writers before undo.

`test_mini_snapshots.py` covers conflicts, corruption, incomplete/legacy records,
reopen, permissions, and repeated undo. CLI tests cover host integration.
