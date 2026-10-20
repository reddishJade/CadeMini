# Log

`SessionLog` owns the append-only JSONL file.

## Create and open

- `SessionLog.create(directory, session_id=None)` creates a unique file and
  writes a `meta` entry.
- `SessionLog.open(path)` validates v1/v2/v3 envelopes, v2/v3 payloads, and
  navigation references. Reads ignore a
  torn final line; the next valid append truncates that tail.
- The session ID is derived from the file name when not supplied.

## Append

`append(type, data)`:

1. Validates the v3 payload without modifying the file.
2. Takes an exclusive file lock.
3. Reads current entries and validates the selected parent and references.
4. Builds a v3 entry, then repairs a torn tail only after validation succeeds.
5. Appends one JSON line, flushes, and calls `fsync`.
6. Releases the lock.

`fsync` is deliberate: a returned append is durable enough to resume. A failed
write raises `LogError` and does not return a fake entry.

`run_lock()` protects a whole turn separately from individual appends. It rejects
competing operations immediately with `LogError`. `run()`, manual CLI context
changes, navigation, and path copying use it; other multi-event storage clients
must acquire it explicitly.

## Branch

`branch()` follows parent IDs from the last entry back to the root and reverses
the list. `branch(entry_id)` reads the path ending at a historical entry. Reads
reject cycles, missing parents, duplicate IDs, and invalid navigation targets.

`switch_branch(selected_id)` appends a v3 navigation event in the same file.
User selection continues before that input and exposes its text through
`navigation_draft`; other selection continues after the entry with an empty
draft. A subsequent user or assistant entry consumes the draft. `history_tree()`
provides a pure, depth-annotated view of all visible branches.

`copy_branch()` copies one connected path, including an inactive path selected
for fork, under the source run lock. It remaps IDs and context cuts, preserves
versions and timestamps, and omits navigation controls and siblings. The copy
is written as one snapshot, or remains deferred when it has no conversation.

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
- The schema version is closed; new event types require a decision record.

## Tests

`test_mini_log.py` covers create, append, resume, tail repair, and listing.
`test_mini_session_history.py` covers navigation, old-format recovery, inactive
forks, and independent copies. `test_mini_projection.py` covers malformed paths.
`test_mini_history_e2e.py` verifies the public commands through real TUI input.
