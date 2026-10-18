# Log

`SessionLog` owns the append-only JSONL file.

## Create and open

- `SessionLog.create(directory, session_id=None)` creates a unique file and
  writes a `meta` entry.
- `SessionLog.open(path)` validates the envelope and v2 payloads. Reads ignore a
  torn final line; the next valid append truncates that tail.
- The session ID is derived from the file name when not supplied.

## Append

`append(type, data)`:

1. Validates the v2 payload without modifying the file.
2. Takes an exclusive file lock.
3. Repairs a torn tail and reads the current entries to get the parent ID.
4. Builds an entry with ID, timestamp, parent, type, and data.
5. Appends one JSON line, flushes, and calls `fsync`.
6. Releases the lock.

`fsync` is deliberate: a returned append is durable enough to resume. A failed
write raises `LogError` and does not return a fake entry.

`run_lock()` protects a whole turn separately from individual appends. It rejects
competing operations immediately with `LogError`. `run()` and manual CLI context
changes use it; other multi-event storage clients must acquire it explicitly.

## Branch

`branch()` follows parent IDs from the last entry back to the root and reverses
the list. It rejects cycles, missing parents, and duplicate IDs.

## Listing and titles

`list_sessions(directory)` returns session ID, update time, and a title derived
from the first user text. Corrupt files are skipped so one bad session cannot
break the list.

## Invariants

- Entries are never rewritten in place.
- IDs are unique within a file.
- A parent precedes its child.
- The file ends with exactly one newline.
- The schema version is closed; new event types require a decision record.

## Tests

`test_mini_log.py` covers create, append, resume, tail repair, branching, and
listing. `test_mini_projection.py` covers malformed branches.
