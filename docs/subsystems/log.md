# Log

`SessionLog` owns the append-only JSONL file.

## Create and open

- `SessionLog.create(directory, session_id=None)` creates a unique file and
  writes a `meta` entry.
- `SessionLog.open(path)` validates envelopes, payloads, and
  navigation references. Reads ignore a
  torn final line; the next valid append truncates that tail.
- The session ID is derived from the file name when not supplied.

## Append

`append(type, data)`:

1. Validates the payload without modifying the file.
2. Takes an exclusive file lock.
3. Checks file identity, size, and nanosecond timestamps. Reuses validated
   entries when unchanged; otherwise reloads and validates the complete file.
4. Builds an entry and validates its parent and references against the verified
   ID index, then repairs a torn tail only after validation succeeds.
5. Appends one JSON line, flushes, and calls `fsync`.
6. Releases the lock.

`fsync` is deliberate: a returned append is durable enough to resume. A failed
write raises an error and does not return a fake entry.

The cache is private and updated only after a successful write and sync. Another
writer, file replacement, or truncation invalidates it when file metadata changes.
`entries()` and `branch()` return independent copies, including nested payloads.
The cache adds no durable state and does not change request audits.

`run_lock()` protects a whole turn separately from individual appends. It rejects
competing operations immediately with `LogError`. `run()`, manual CLI context
changes, navigation, and path copying use it; other multi-event storage clients
must acquire it explicitly.

## Branch

`branch()` follows parent IDs from the last entry back to the root and reverses
the list. `branch(entry_id)` reads the path ending at a historical entry. Reads
reject cycles, missing parents, duplicate IDs, and invalid navigation targets.

`switch_branch(selected_id)` appends a navigation event in the same file.
User selection continues before that input and exposes its text through
`navigation_draft`; other selection continues after the entry with an empty
draft. A subsequent user or assistant entry consumes the draft. `history_tree()`
provides a pure, depth-annotated view of all visible branches.

`copy_branch()` copies one connected path, including an inactive path selected
for fork, under the source run lock. It remaps IDs and context cuts, preserves
timestamps, and omits navigation/checkpoint/recovery controls and siblings. The copy
is written as one snapshot, or remains deferred when it has no conversation.

Log-only checkpoint and restore events coordinate recovery. Checkpoints link an earlier
user entry and pre-turn ancestor. Restore parents identify the committed target;
code-only restore must preserve the preceding head. File effects and LIFO state
belong to the workspace coordinator, not conversation projection.

## Listing and titles

`list_sessions(directory)` returns session ID, update time, and a title derived
from the first user text across the full file, even when navigation currently
precedes that input. Corrupt files are skipped so one bad session cannot
break the list. Update time and ordering use file modification time, so copied
historical timestamps do not make a newly created session appear old.

## Invariants

- Entries are never rewritten in place.
- IDs are unique within a file.
- A parent precedes its child.
- The file ends with exactly one newline.
- The schema is closed; new event types require a decision record.

## Tests

`test_log.py` covers create, append, resume, tail repair, cache isolation,
external changes, failed sync, and listing.
`test_session_history.py` covers navigation, old-format recovery, inactive
forks, and independent copies. `test_projection.py` covers malformed paths.
`test_history_e2e.py` verifies the public commands through real TUI input.
